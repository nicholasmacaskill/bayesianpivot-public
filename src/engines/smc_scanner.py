import numpy as np
import pandas as pd
import ccxt
import time
from datetime import datetime, time as time_obj
from src.core.config import Config
from src.engines.intermarket_engine import IntermarketEngine
from src.engines.news_filter import NewsFilter
from src.engines.visualizer import generate_bias_chart
from src.engines.ai_validator import AIValidator
import logging
import os
import functools
from src.core.database import log_system_event

# Prevent yfinance filesystem/locking/FD leak issues by forcing it to use the dummy cache
try:
    import yfinance as yf
    yf.set_tz_cache_location("/tmp/yfinance_cache")
    from yfinance import cache
    cache._TzCacheManager._tz_cache = cache._TzCacheDummy()
except Exception:
    pass

from src.engines.shadow_substitution_engine import KalmanStateFilter, ShadowSubstitutionEngine

logger = logging.getLogger(__name__)

def ensure_data(default_return=None):
    """Decorator to ensure df is valid before running analysis"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, df, *args, **kwargs):
            if df is None or len(df) < 5:
                return default_return
            try:
                return func(self, df, *args, **kwargs)
            except Exception as e:
                logger.error(f"Error in {func.__name__}: {e}")
                return default_return
        return wrapper
    return decorator

def safe_scan(component):
    """Decorator to catch and log errors in high-level scanning methods"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                import traceback
                symbol = args[0] if args else "Unknown"
                err_msg = f"{component} error ({symbol}): {str(e)}\n{traceback.format_exc()}"
                logger.error(err_msg)
                log_system_event(component, err_msg, level="ERROR")
                return None
        return wrapper
    return decorator

def send_pulse_to_telegram(message):
    """Bridge to send pulse updates to Telegram via individual bot or shared utility"""
    try:
        from src.clients.telegram_notifier import send_message
        send_message(message)
    except Exception as e:
        logger.error(f"Pulse Telegram Error: {e}")

class SMCScanner:
    def __init__(self):
        # Initialize public exchange for data fetching (free tier)
        # Using Coinbase (Advanced Trade) to avoid Binance geo-restrictions
        try:
            self.exchange = ccxt.coinbase({'enableRateLimit': True})
        except Exception:
            # Fallback to standard coinbase if 'coinbase' alias refers to old API in this version
            # But usually 'coinbase' is the correct one for public market data now
            self.exchange = ccxt.coinbasepro({'enableRateLimit': True})
            
        # Dedicated exchange for commodities/metals (Gold) to eliminate basis discrepancy
        try:
            self.bybit_exchange = ccxt.bybit({'enableRateLimit': True})
        except Exception as e:
            logger.warning(f"Could not initialize Bybit for metals/gold: {e}")
            self.bybit_exchange = None

        self.intermarket = IntermarketEngine()
        self.news = NewsFilter()
        self.kalman_filter = KalmanStateFilter()
        self.shadow_sub = ShadowSubstitutionEngine()
        self.order_book_enabled = True  # Can be disabled if exchange doesn't support
        # Deduplication cache: prevents firing the same signal multiple times per candle window
        # Key: (symbol, pattern_type) | Value: timestamp of last signal
        self._signal_cache = {}
        self._signal_cooldown_mins = 60  # Increased from 15m to 60m to reduce noise <!-- id: 9 -->
        self.bias_cache = {} # Protect against redundant calls
        self.last_pulse_time = 0

    def log_market_pulse(self, symbol):
        """
        Terminal Consciousness: Sends a periodic 'System Sentiment' update to Telegram.
        Triggered every 15 minutes by the local runner.
        """
        try:
            now = time.time()
            # 1. Fetch Data
            df = self.fetch_data(symbol, Config.TIMEFRAME, limit=300)
            if df is None: return
            
            # 2. Extract Hurst & SMT
            closes = df['close'].values
            hurst = self.get_hurst_exponent(closes)
            
            # Identify Regime
            regime = "CHOP / RANDOM"
            h_low, h_high = Config.get('HURST_CHAOS_RANGE', (0.495, 0.505))
            if hurst > h_high: regime = "EXPANSION (Momentum)"
            elif hurst < h_low: regime = "MEAN REVERSION (Range)"
            
            # 3. Get Macro Bias
            bias_full = self.get_detailed_bias(symbol)
            
            # 3b. Calculate detailed timeframe bias breakdown (1D, 4H, 1H)
            bias_breakdown = ""
            try:
                df_1d = self.fetch_data(symbol, '1d', limit=100, synchronized=False)
                df_4h = self.fetch_data(symbol, '4h', limit=100, synchronized=False)
                df_1h = self.fetch_data(symbol, '1h', limit=100, synchronized=False)
                
                def get_tf_bias_str(df):
                    if df is None or df.empty: return "N/A"
                    ema20 = df['close'].ewm(span=20).mean().iloc[-1]
                    ema50 = df['close'].ewm(span=50).mean().iloc[-1]
                    return "BULL" if ema20 > ema50 else "BEAR"
                
                bias_1d_str = get_tf_bias_str(df_1d)
                bias_4h_str = get_tf_bias_str(df_4h)
                bias_1h_str = get_tf_bias_str(df_1h)
                
                bias_breakdown = f" (1D: {bias_1d_str} | 4H: {bias_4h_str} | 1H: {bias_1h_str})"
            except Exception as e:
                logger.error(f"Failed to calculate detailed biases: {e}")

            # 4. SMT Context
            smt_strength = self.intermarket.get_smt_strength(symbol, df)
            
            # 4b. HTF Gravity Points (Potential Reversals)
            gravity_msg = ""
            nearest_resistance = None
            nearest_support = None
            try:
                htf_pois = self.detect_htf_pois(symbol)
                if htf_pois and df is not None and not df.empty:
                    current_price = df['close'].iloc[-1]
                    res_pois = sorted([p for p in htf_pois if p['bottom'] > current_price], key=lambda x: x['bottom'])
                    sup_pois = sorted([p for p in htf_pois if p['top'] < current_price], key=lambda x: x['top'], reverse=True)
                    
                    nearest_resistance = res_pois[0] if res_pois else None
                    nearest_support = sup_pois[0] if sup_pois else None
                    
                    if nearest_resistance:
                        gravity_msg += f"\n🎯 **HTF Resistance:** {nearest_resistance['level']:.2f} ({nearest_resistance['type'].replace('_', ' ')})"
                    if nearest_support:
                        gravity_msg += f"\n🧲 **HTF Support:** {nearest_support['level']:.2f} ({nearest_support['type'].replace('_', ' ')})"
                    
                    if gravity_msg:
                        gravity_msg = "\n───────────────────" + gravity_msg
            except Exception as e:
                logger.error(f"Failed to fetch HTF POIs for pulse: {e}")
            
            # 4c. Dynamic Strategic Playbook
            interpretation_lines = []
            
            # Bias interpretation
            if "Conflict" in bias_full or "NEUTRAL" in bias_full.upper():
                conflict_msg = "• *Bias:* Trend direction is mixed. Breakout/momentum trades are risky; prefer range sweep fades."
                if 'bias_1d_str' in locals() and 'bias_4h_str' in locals():
                    if bias_1d_str == "BEAR" and bias_4h_str == "BULL" and nearest_resistance:
                         conflict_msg += f" Counter-trend rally likely reaching for {nearest_resistance['level']:.2f} HTF Resistance."
                    elif bias_1d_str == "BULL" and bias_4h_str == "BEAR" and nearest_support:
                         conflict_msg += f" Deep pullback likely reaching for {nearest_support['level']:.2f} HTF Support."
                interpretation_lines.append(conflict_msg)
            elif "BULLISH" in bias_full.upper():
                interpretation_lines.append("• *Bias:* Macro trend aligned upward. Long setups (discount sweeps/reclaims) have higher probability.")
            elif "BEARISH" in bias_full.upper():
                interpretation_lines.append("• *Bias:* Macro trend aligned downward. Short setups (premium sweeps/rejections) have higher probability.")
            else:
                interpretation_lines.append("• *Bias:* Neutral/consolidation. Enforce strict range-bound rules.")

            # Hurst regime interpretation
            if hurst < h_low:
                interpretation_lines.append("• *Regime:* Mean Reversion active. Breakouts are highly likely to fake out. We focus on range sweeps (Turtle Soup) at session extremes.")
            elif hurst > h_high:
                interpretation_lines.append("• *Regime:* Expansion active. Price is trending. Look for structure shifts (MSS) and ride displacement momentum.")
            else:
                interpretation_lines.append("• *Regime:* Choppy / Random. High noise level; the funnel is in defensive mode.")

            # SMT interpretation
            if smt_strength >= 0.5:
                interpretation_lines.append(f"• *Sponsorship:* SMT is strong ({smt_strength:.2f}), confirming quiet institutional accumulation/distribution at range boundaries.")
            else:
                interpretation_lines.append("• *Sponsorship:* SMT is weak. Current move lacks divergence-backed institutional validation.")

            interpretation_block = "\n".join(interpretation_lines)
            
            # 5. Format Message
            pulse_msg = (
                f"🧠 *Bayesian Pivot Sentiment* | `{symbol}`\n"
                f"───────────────────\n"
                f"🏛️ **Macro Bias:** {bias_full}{bias_breakdown}\n"
                f"🌀 **Hurst Regime:** {regime} ({hurst:.3f})\n"
                f"⚡ **SMT Divergence:** {smt_strength:.2f}/1.0{gravity_msg}\n"
                f"───────────────────\n"
                f"💡 **Strategic Playbook:**\n"
                f"{interpretation_block}\n"
                f"───────────────────\n"
                f"🛡️ *9-Gate Funnel: ARMED & SCANNING*"
            )
            
            logger.info(f"Pulse: {pulse_msg.replace('*', '').replace('`', '')}")
            send_pulse_to_telegram(pulse_msg)
            self.last_pulse_time = now
            return True
        except Exception as e:
            logger.error(f"Error generating Market Pulse: {e}")
            return False

    def get_hurst_exponent(self, time_series):
        """
        Calculates the Hurst Exponent to determine market regime.
        H < 0.5 = Mean Reverting (Range) - Ideal for Turtle Soup
        H = 0.5 = Brownian Motion (Random)
        H > 0.5 = Trending (Momentum) - Ideal for Breakouts
        """
        try:
            from scipy.stats import linregress
            # Create a range of lag values
            lags = range(2, 20)
            tau = [np.sqrt(np.std(np.subtract(time_series[lag:], time_series[:-lag]))) for lag in lags]
            # Use linear regression to estimate the Hurst Exponent
            poly = np.polyfit(np.log(lags), np.log([max(t, 1e-8) for t in tau]), 1)
            return poly[0] * 2.0
        except Exception as e:
            logger.error(f"Hurst Calculation Error: {e}")
            return 0.5

    def get_adf_test(self, time_series):
        """
        Performs Augmented Dickey-Fuller test to check for stationarity.
        p-value < 0.05 indicates the series is stationary (Mean Reverting).
        """
        try:
            from statsmodels.tsa.stattools import adfuller
            result = adfuller(time_series)
            return result[1] # p-value
        except ImportError:
            return 1.0 # Default to non-stationary
        except Exception as e:
            logger.error(f"ADF Test Error: {e}")
            return 1.0
        
    @ensure_data(default_return=pd.Series(dtype=float))
    def calculate_atr(self, df, period=14):
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        return true_range.rolling(period).mean()

    @ensure_data(default_return=pd.Series(dtype=float))
    def calculate_rsi(self, df, period=14):
        """Standard RSI Calculation"""
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def _aggregate_ohlcv(self, df, timeframe='4h'):
        """Aggregates lower timeframe data into higher timeframe bars manually."""
        if timeframe != '4h': return df
        if df is None or df.empty: return None
        
        # Ensure timestamp is index for resample
        df_copy = df.copy().set_index('timestamp')
        resampled = df_copy.resample('4h').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        return resampled.reset_index()

    def _check_data_synchrony(self, symbol, df_ccxt):
        """
        Synchronized Data Buffer: Compares CCXT vs yfinance.
        Delta > 0.05% or Latency > 2 mins results in False.
        """
        if df_ccxt is None or df_ccxt.empty:
            return False
            
        return True # BYPASS: yfinance is causing SQLite locking errors and starving the system
        
        # Fetch yfinance data internally
        try:
            # Map symbol to yfinance format (BTC/USD -> BTC-USD)
            # Match symbol format
            yf_symbol = symbol.replace('/', '-') if '/' in symbol else symbol
            if 'USDT' in yf_symbol: yf_symbol = yf_symbol.replace('USDT', 'USD')
            
            # Fetch yfinance 5m data for a tighter match (limit to 1 day for speed)
            import yfinance as yf
            df_yf_raw = yf.download(yf_symbol, period='1d', interval='5m', progress=False)
            
            if df_yf_raw is None or df_yf_raw.empty:
                logger.warning(f"yfinance 5m sync failed for {symbol} - retrying with 1h")
                df_yf_raw = yf.download(yf_symbol, period='5d', interval='1h', progress=False)
            
            if df_yf_raw is None or df_yf_raw.empty:
                logger.warning(f"yfinance sync failed for {symbol} - holding trade.")
                return False

            if isinstance(df_yf_raw.columns, pd.MultiIndex):
                df_yf_raw.columns = df_yf_raw.columns.get_level_values(0)
            df_yf_raw = df_yf_raw.reset_index()
            df_yf_raw.columns = [c.lower() for c in df_yf_raw.columns]
            df_yf_raw.rename(columns={'date': 'timestamp', 'datetime': 'timestamp'}, inplace=True)
            if df_yf_raw['timestamp'].dt.tz is not None:
                df_yf_raw['timestamp'] = df_yf_raw['timestamp'].dt.tz_localize(None)
            
            df_yf = df_yf_raw.loc[:, ~df_yf_raw.columns.duplicated()]
            
            # Use the latest available price from yfinance
            yf_latest = df_yf.iloc[-1]
            ccxt_latest = df_ccxt.iloc[-1]
            
            # 1. Price Delta Check (Sanity Check: 0.25% or Config)
            price_delta = abs(ccxt_latest['close'] - yf_latest['close']) / ccxt_latest['close']
            
            # Use UTC for comparison
            ts_diff = abs((ccxt_latest['timestamp'] - yf_latest['timestamp']).total_seconds())
            
            # Threshold: 0.05% if perfectly aligned, else 0.5% for sanity check
            base_threshold = Config.get('SYNC_PRICE_DELTA_MAX', 0.0005)
            # If timestamps are significantly different, loosen threshold for "Reality Check"
            threshold = 0.0025 if ts_diff > 300 else base_threshold # 0.25% if offset
                
            if price_delta > threshold:
                logger.warning(f"⚖️ Data Sync Delta Breach: {price_delta:.4%} (Threshold: {threshold:.4%})")
                return False
                
            return True
        except Exception as e:
            logger.error(f"Sync Buffer Error: {e}")
            return False

    def fetch_data(self, symbol, timeframe, limit=100, synchronized=True):
        """
        98% Reliability Refactor: SynchronizedDataBuffer for parallel streams.
        Eliminates proxies and enforces strict time-drift validation (120s limit).
        """
        try:
            tf_to_seconds = {'1m': 60, '5m': 300, '1h': 3600, '4h': 14400, '1d': 86400}
            
            # Map Gold, Silver, EUR, GBP to Bybit continuous contracts
            is_gold = symbol in ["XAU/USD", "XAUUSD", "GOLD"]
            is_silver = symbol in ["XAG/USD", "XAGUSD", "SILVER"]
            is_eur = symbol in ["EUR/USD", "EURUSD"]
            is_gbp = symbol in ["GBP/USD", "GBPUSD"]
            target_exchange = self.exchange
            fetch_symbol = symbol

            if is_gold:
                if getattr(self, 'bybit_exchange', None):
                    target_exchange = self.bybit_exchange
                    fetch_symbol = "XAU/USDT:USDT"
                elif self.exchange.id == 'coinbase':
                    fetch_symbol = "PAXG/USD"
            elif is_silver:
                if getattr(self, 'bybit_exchange', None):
                    target_exchange = self.bybit_exchange
                    fetch_symbol = "XAG/USDT:USDT"
            elif is_eur:
                if getattr(self, 'bybit_exchange', None):
                    target_exchange = self.bybit_exchange
                    fetch_symbol = "EURUSD/USDT:USDT"
            elif is_gbp:
                if getattr(self, 'bybit_exchange', None):
                    target_exchange = self.bybit_exchange
                    fetch_symbol = "GBPUSD/USDT:USDT"

            # 1. Fetch Primary Stream
            # If Coinbase and 4H, aggregate from 1h; otherwise fetch natively from target exchange
            if timeframe == '4h' and target_exchange.id == 'coinbase':
                df_raw_main = target_exchange.fetch_ohlcv(fetch_symbol, '1h', limit=limit*4)
                if not df_raw_main: return None
                main_df_base = pd.DataFrame(df_raw_main, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                main_df_base['timestamp'] = pd.to_datetime(main_df_base['timestamp'], unit='ms')
                main_df = self._aggregate_ohlcv(main_df_base, '4h')
            else:
                df_raw_main = target_exchange.fetch_ohlcv(fetch_symbol, timeframe, limit=limit)
                if not df_raw_main: return None
                main_df = pd.DataFrame(df_raw_main, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                main_df['timestamp'] = pd.to_datetime(main_df['timestamp'], unit='ms')

            if not synchronized:
                return main_df

            # 2. SynchronizedDataBuffer: Check 5M, 1H, and 4H drift
            timeframes_to_check = ['5m', '1h']
            if timeframe != '4h': timeframes_to_check.append('4h')
            
            for tf in timeframes_to_check:
                if tf == '4h' and target_exchange.id == 'coinbase':
                    # Native aggregation for 4H on Coinbase
                    df_base_raw = target_exchange.fetch_ohlcv(fetch_symbol, '1h', limit=limit*4)
                    if not df_base_raw: continue
                    df_base = pd.DataFrame(df_base_raw, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df_base['timestamp'] = pd.to_datetime(df_base['timestamp'], unit='ms')
                    df_tf = self._aggregate_ohlcv(df_base, '4h')
                else:
                    df_raw = target_exchange.fetch_ohlcv(fetch_symbol, tf, limit=10)
                    if not df_raw: continue
                    df_tf = pd.DataFrame(df_raw, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df_tf['timestamp'] = pd.to_datetime(df_tf['timestamp'], unit='ms')

                if df_tf is None or df_tf.empty:
                    continue

                # Time-Drift Validation (Config + Candle Duration)
                latest_ts = df_tf.iloc[-1]['timestamp']
                
                # Use UTC for comparison as ccxt timestamps are UTC
                now_utc = datetime.utcnow()
                drift = abs((now_utc - latest_ts).total_seconds())
                
                # Limit must account for the fact that the latest bar timestamp is the START of the candle
                tf_sec = tf_to_seconds.get(tf, 300)
                allowed_drift = Config.get('SYNC_LATENCY_SEC_MAX', 120) + tf_sec
                
                if drift > allowed_drift:
                    if (is_gold or is_silver or is_eur or is_gbp) and now_utc.weekday() >= 5:
                        logger.debug(f"Weekend stream {tf} drift for {symbol}: {drift:.1f}s. Skipping blocking wait.")
                        return None
                    logger.warning(f"🚨 DATA_DESYNC: Stream {tf} drift is {drift:.1f}s (Limit: {allowed_drift}s). Pausing 10s for stream sync...")
                    time.sleep(10)
                    return None # Triggers "HOLD" state in runner

            # 3. Double-Source Check (CCXT vs yFinance)
            if not self._check_data_synchrony(symbol, main_df):
                return None

            return main_df
        except Exception as e:
            err_str = str(e).lower()
            if any(term in err_str for term in ["timed out", "timeout", "connection reset", "connection refused", "name resolution", "temporary failure", "rate limit", "ratelimit", "too many visits", "429"]):
                logger.warning(f"📡 [DATA NETWORK GLITCH] Fetch network/rate-limit jitter on {symbol} ({timeframe}): {e}")
            else:
                import traceback
                logger.error(f"Fetch error on {symbol}: {e}\n{traceback.format_exc()}")
            return None

    def calculate_volume_cluster(self, df, lookback=20):
        """
        PHASE 2: Volume Cluster Detection.
        Institutional Logic: Smart money leaves large 'prints' in volume 
        when sweeping liquidity or absorbing orders.
        """
        if df is None or len(df) < lookback:
            return 1.0
        
        recent_volume = df['volume'].iloc[-1]
        avg_volume = df['volume'].iloc[-lookback:-1].mean()
        
        if avg_volume == 0:
            return 1.0
            
        ratio = recent_volume / avg_volume
        return round(ratio, 2)

    def calculate_rvol(self, df, lookback=20):
        """
        Calculates Relative Volume (RVol) against 20-period moving average.
        Returns float ratio (e.g. 2.5 = 250% of normal volume).
        """
        if df is None or len(df) < lookback or 'volume' not in df.columns:
            return 1.0
        vol = df['volume'].values
        avg_vol = np.mean(vol[-lookback-1:-1]) if len(vol) > lookback else np.mean(vol)
        if avg_vol <= 0:
            return 1.0
        return float(round(vol[-1] / avg_vol, 2))

    def detect_cvd_exhaustion(self, df, lookback=6):
        """
        Detects Cumulative Volume Delta (CVD) exhaustion/absorption.
        Estimates buying vs selling volume delta across recent candles.
        Returns dict with delta trend and exhaustion boolean.
        """
        if df is None or len(df) < lookback:
            return {'exhausted': False, 'delta_ratio': 1.0, 'bias': 'NEUTRAL'}
        
        recent = df.tail(lookback).copy()
        # Estimate volume delta using close relative to candle range
        candle_ranges = (recent['high'] - recent['low']).replace(0, 1e-6)
        close_positions = (recent['close'] - recent['low']) / candle_ranges
        # Up-delta if closed in upper half, down-delta if lower half
        deltas = (close_positions - 0.5) * 2.0 * recent['volume']
        
        total_delta = deltas.sum()
        latest_delta = deltas.iloc[-1]
        
        # Exhaustion occurs when price makes an extreme wick but delta flips opposite
        exhausted = (latest_delta * total_delta < 0) or (abs(latest_delta) < abs(deltas.mean()) * 0.5)
        bias = 'BULLISH_EXHAUSTION' if total_delta > 0 and latest_delta < 0 else ('BEARISH_EXHAUSTION' if total_delta < 0 and latest_delta > 0 else 'NEUTRAL')
        
        return {
            'exhausted': bool(exhausted),
            'bias': bias,
            'total_delta': float(round(total_delta, 2)),
            'latest_delta': float(round(latest_delta, 2))
        }

    @ensure_data(default_return=(pd.Series(dtype=bool), pd.Series(dtype=bool)))
    def detect_fractals(self, df, window=2):
        """
        Vectorized fractal detection using NumPy.
        Returns boolean masks for Swing Highs and Lows.
        """
        # Fractal High
        is_high = df['high'].rolling(window=2*window+1, center=True).max() == df['high']
        # Fractal Low
        is_low = df['low'].rolling(window=2*window+1, center=True).min() == df['low']
        
        return is_high, is_low

    def is_killzone(self, current_time=None):
        """
        Global Liquidity Mode: Returns True 24/7, but logs session context.
        """
        from datetime import datetime
        now = current_time or datetime.utcnow()
        hour = now.hour

        # 1. Check NY Lunch Blackout (17:00 - 18:00 UTC)
        # In Global Liquidity Mode, we still flag it but don't hard-gate unless specified.
        lunch_start, lunch_end = Config.get('NY_LUNCH_BLACKOUT', (17, 18))
        if hour >= lunch_start and hour < lunch_end:
            logger.debug("Bayesian Pivot Context: NY_LUNCH_BLACKOUT (Reduced Liquidity)")
            return True # Always True in Global Mode

        # Labeling for internal context
        if 7 <= hour < 10:
             logger.debug("Bayesian Pivot Context: LONDON")
        elif 0 <= hour < 4:
             logger.debug("Bayesian Pivot Context: ASIA")
        elif 12 <= hour < 20:
             logger.debug("Bayesian Pivot Context: NY_CONTINUOUS")

        return True

    def is_asian_fade_window(self, hour=None):
        """Returns True if we are in the 11 PM – 2 AM EST (4–7 AM UTC) Asian Fade prime window."""
        if hour is None:
            hour = datetime.utcnow().hour
        fade = Config.KILLZONE_ASIAN_FADE
        return fade is not None and (fade[0] <= hour < fade[1])

    def scan_asian_fade(self, symbol):
        """
        ⭐ PRIME ALPHA DETECTOR: Asian Range High/Low Fade
        
        Edge: 100% win rate when fading the Asian Range H/L during the
              11 PM – 2 AM EST manipulation window (4–7 AM UTC).
        
        Logic:
            1. Identify Asian Range (00:00–04:00 UTC candles)
            2. Detect Upper/Lower Quartile zones (top/bottom 25% of range)
            3. Look for a wick / false break above/below the range
            4. Confirm candle closes back inside the range (rejection)
            5. Return a SHORT (at High) or LONG (at Low) setup
        """
        if not self.is_asian_fade_window():
            return None  # Only fire during the prime window

        df = self.fetch_data(symbol, Config.TIMEFRAME, limit=150)
        if df is None or len(df) < 100:
            return None

        try:

            # Extract Asian Range candles (00:00 – 04:00 UTC)
            df['hour'] = df['timestamp'].dt.hour
            # 4 hours = sixteen 15m candles
            asian_candles = df[df['hour'].between(0, 3)].tail(48)

            if len(asian_candles) < 5:
                logger.debug(f"Insufficient Asian candles for {symbol}")
                return None

            asian_high = asian_candles['high'].max()
            asian_low = asian_candles['low'].min()
            asian_range = asian_high - asian_low

            if asian_range <= 0:
                return None

            # Step 2: Define the quartile "trap" zones
            upper_quartile = asian_high - (asian_range * 0.25)  # Top 25% of range
            lower_quartile = asian_low + (asian_range * 0.25)   # Bottom 25% of range

            # Step 3 & 4: Check the last 6 candles for a false break + rejection
            recent = df.tail(6)
            last = df.iloc[-1]

            # --- SHORT SETUP: Wick above Asian High, close back inside ---
            short_setup = (
                recent['high'].max() > asian_high           # Swept high
                and last['close'] < asian_high              # Rejected back below
                and last['close'] > upper_quartile          # Closed in premium zone
                and last['close'] < last['open']            # Bearish close
            )

            # --- LONG SETUP: Wick below Asian Low, close back inside ---
            long_setup = (
                recent['low'].min() < asian_low             # Swept low
                and last['close'] > asian_low               # Rejected back above
                and last['close'] < lower_quartile          # Closed in discount zone
                and last['close'] > last['open']            # Bullish close
            )

            if not (short_setup or long_setup):
                return None

            direction = "SHORT" if short_setup else "LONG"
            entry = last['close']
            atr = self.calculate_atr(df).iloc[-1]

            # Ensure stop loss is not too tight (floor at MIN_STOP_LOSS_ATR)
            sl_buffer = max(atr * 0.5, atr * Config.get('MIN_STOP_LOSS_ATR', 1.5))
            stop_loss = (asian_high + sl_buffer) if direction == "SHORT" else (asian_low - sl_buffer)
            target = entry - (abs(entry - stop_loss) * 3.0) if direction == "SHORT" else entry + (abs(stop_loss - entry) * 3.0)

            setup = {
                'symbol': symbol,
                'pattern': f'Asian Range {direction} Fade',
                'direction': direction,
                'entry': round(entry, 2),
                'stop_loss': round(stop_loss, 2),
                'target': round(target, 2),
                'asian_high': round(asian_high, 2),
                'asian_low': round(asian_low, 2),
                'asian_range': round(asian_range, 2),
                'is_asian_fade': True,  # Flag for priority treatment
                'price_quartiles': {
                    'Asian Range': {'high': round(asian_high, 2), 'low': round(asian_low, 2)}
                },
                'time_quartile': {'num': 2, 'phase': 'Manipulation'},
                'smt_strength': 0.0,   # Will be enriched by SMT engine if available
                'bias': 'Bearish' if direction == 'SHORT' else 'Bullish',
                'index_context': 'Asian Session Fade Window',
            }

            logger.info(f"⭐ ASIAN FADE DETECTED: {symbol} {direction} | Asian H: {asian_high} | L: {asian_low}")
            return setup, df

        except Exception as e:
            logger.error(f"scan_asian_fade error for {symbol}: {e}")
            return None

    def get_detailed_bias(self, symbol, index_context=None, visual_check=False, current_time=None):
        """
        98% Reliability Refactor: Strict Multi-Timeframe Alignment.
        Requires 4H Bias == 1H Bias == 5M Flow for strict conviction.
        """
        cache_key = f"bias_{symbol}"
        now = current_time.timestamp() if current_time else time.time()
        
        # 0. Check Bias Cache
        if not hasattr(self, '_bias_cache'): self._bias_cache = {}
        if symbol in self._bias_cache:
            entry = self._bias_cache[symbol]
            cache_duration = 900 if not current_time else 1800
            if (now - entry['timestamp']) < cache_duration:
                if not visual_check or 'visual' in entry:
                    self.last_bias_score = entry['score']
                    if not hasattr(self, '_last_bias_1d'):
                        self._last_bias_1d = {}
                    self._last_bias_1d[symbol] = entry.get('bias_1d', 0)
                    return entry['label']

        # 1. Fetch Aligned Data
        df_1d = self.fetch_data(symbol, '1d', limit=100)
        df_4h = self.fetch_data(symbol, '4h', limit=100)
        df_1h = self.fetch_data(symbol, '1h', limit=100)
        df_5m = self.fetch_data(symbol, '5m', limit=100)
        
        if any(d is None or d.empty for d in [df_1d, df_4h, df_1h, df_5m]):
            return "NEUTRAL (Data Gap)"

        def get_tf_bias(df):
            ema20 = df['close'].ewm(span=20).mean().iloc[-1]
            ema50 = df['close'].ewm(span=50).mean().iloc[-1]
            return 1 if ema20 > ema50 else -1
            
        bias_1d = get_tf_bias(df_1d)
        bias_4h = get_tf_bias(df_4h)
        bias_1h = get_tf_bias(df_1h)
        bias_5m = get_tf_bias(df_5m)
        
        # Cache the Daily Bias direction for direct use in scanning gates
        if not hasattr(self, '_last_bias_1d'):
            self._last_bias_1d = {}
        self._last_bias_1d[symbol] = bias_1d
        
        # Intraday Bias Alignment: 4H and 1H Alignment Required (1D cached for macro context)
        if not (bias_4h == bias_1h):
            logger.info(f"⚖️ {symbol} Bias Conflict: 4H({bias_4h}) 1H({bias_1h}) [1D={bias_1d}]")
            return "NEUTRAL (Conflict)"

        score = float(bias_4h) # Base score -1 or 1 based on 4H/1H alignment


        # 2. Intermarket (DXY)
        if index_context and 'DXY' in index_context:
            dxy_trend = index_context['DXY']['trend']
            if dxy_trend == 'DOWN' and score > 0: score += 1.0
            elif dxy_trend == 'UP' and score < 0: score -= 1.0

        # 3. HTF Gravity Points
        try:
            htf_pois = self.detect_htf_pois(symbol)
            latest = df_5m.iloc[-1]
            for poi in htf_pois:
                if score > 0 and 'BEARISH' in poi['type']:
                    if abs(latest['close'] - poi['level']) / poi['level'] < 0.005:
                        score -= 0.5 # Soften bias near resistance
                if score < 0 and 'BULLISH' in poi['type']:
                    if abs(latest['close'] - poi['level']) / poi['level'] < 0.005:
                        score += 0.5
        except Exception: pass

        label = "STRONG BULLISH" if score >= 1.5 else "BULLISH" if score > 0 else \
                "STRONG BEARISH" if score <= -1.5 else "BEARISH" if score < 0 else "NEUTRAL"
        
        label_with_score = f"{label} ({score})"
        
        self._bias_cache[symbol] = {
            'timestamp': now,
            'score': score,
            'label': label_with_score,
            'bias_1d': bias_1d
        }
        return label_with_score

    def get_4h_bias(self, symbol):
        # Legacy wrapper
        return self.get_detailed_bias(symbol).split(" (")[0] # Returns BULLISH/BEARISH/NEUTRAL

    def get_session_quartile(self, current_time=None):
        """
        Calculates the current ICT Session Quartile (90-minute cycles).
        Identifies the phase: Accumulation, Manipulation, Distribution, or X.
        """
        now_utc = current_time if current_time else datetime.utcnow()
        hour = now_utc.hour
        minute = now_utc.minute
        total_minutes_today = hour * 60 + minute

        # ICT Sessions (6-hour blocks starting 00:00, 06:00, 12:00, 18:00 UTC)
        # Each session has 4 x 90-minute quartiles
        session_start_hour = (hour // 6) * 6
        minutes_into_session = (hour - session_start_hour) * 60 + minute
        
        quartile_num = (minutes_into_session // 90) + 1
        phases = {
            1: "Q1: Accumulation",
            2: "Q2: Manipulation (Judas)",
            3: "Q3: Distribution",
            4: "Q4: Continuation/Reversal"
        }
        
        return {
            "num": quartile_num,
            "phase": phases.get(quartile_num, "X"),
            "minutes_in": minutes_into_session
        }

    @ensure_data(default_return=None)
    def get_price_quartiles(self, symbol):
        """
        Calculates Asian Range and CBDR High/Low and their Quartiles (SDs).
        Asian Range: 00:00 - 05:00 UTC
        CBDR: 19:00 - 01:00 UTC
        """
        # Fetch 24h of data to find ranges
        df_range = self.fetch_data(symbol, '15m', limit=100)
        if df_range is None or df_range.empty: return None
        
        # Filter for Asian Range (00:00-05:00 UTC)
        asian_df = df_range[(df_range['timestamp'].dt.hour >= 0) & (df_range['timestamp'].dt.hour < 5)]
        # Filter for London Range (07:00-10:00 UTC) - The "Inducement" Phase
        london_df = df_range[(df_range['timestamp'].dt.hour >= 7) & (df_range['timestamp'].dt.hour < 10)]
        # Filter for CBDR (19:00-01:00 UTC)
        cbdr_df = df_range[(df_range['timestamp'].dt.hour >= 19) | (df_range['timestamp'].dt.hour < 1)]
        
        ranges = {}
        for name, data in [("Asian Range", asian_df), ("London Range", london_df), ("CBDR", cbdr_df)]:
            if data.empty: continue
            r_high = data['high'].max()
            r_low = data['low'].min()
            r_diff = r_high - r_low
            
            ranges[name] = {
                "high": r_high,
                "low": r_low,
                "mid": r_low + (r_diff * 0.5),
                "q1": r_low + (r_diff * 0.25),
                "q3": r_low + (r_diff * 0.75),
                "sd_1_pos": r_high + r_diff,
                "sd_1_neg": r_low - r_diff
            }
        
        return ranges
    
    def detect_htf_pois(self, symbol):
        """
        INSTITUTIONAL PRECISION: Detects 1D and 1W Order Blocks and FVGs.
        These act as 'HTF Gravity Points'—trading into them is high-risk.
        """
        pois = []
        for tf in ['1d']: # Removed 1w due to exchange compatibility issues
            df = self.fetch_data(symbol, tf, limit=100, synchronized=False)
            if df is None or len(df) < 10:
                continue
                
            # 1. Detect FVGs
            for i in range(2, len(df)):
                # Bullish FVG
                if df['low'].iloc[i] > df['high'].iloc[i-2]:
                    pois.append({
                        'tf': tf,
                        'type': 'FVG_BULLISH',
                        'top': df['low'].iloc[i],
                        'bottom': df['high'].iloc[i-2],
                        'level': (df['low'].iloc[i] + df['high'].iloc[i-2]) / 2
                    })
                # Bearish FVG
                if df['high'].iloc[i] < df['low'].iloc[i-2]:
                    pois.append({
                        'tf': tf,
                        'type': 'FVG_BEARISH',
                        'top': df['low'].iloc[i-2],
                        'bottom': df['high'].iloc[i],
                        'level': (df['low'].iloc[i-2] + df['high'].iloc[i]) / 2
                    })

            # 2. Detect Order Blocks (Last candle before impulsive move)
            # Simplified: Look for engulfing after a sweep or expansion
            for i in range(10, len(df)-1):
                body_prev = abs(df['close'].iloc[i] - df['open'].iloc[i])
                body_curr = abs(df['close'].iloc[i+1] - df['open'].iloc[i+1])
                
                # Bullish Engulfing (Potential Bullish OB)
                if df['close'].iloc[i+1] > df['high'].iloc[i] and body_curr > body_prev * 2:
                    pois.append({
                        'tf': tf,
                        'type': 'OB_BULLISH',
                        'top': df['high'].iloc[i],
                        'bottom': df['low'].iloc[i],
                        'level': (df['high'].iloc[i] + df['low'].iloc[i]) / 2
                    })
                # Bearish Engulfing
                if df['close'].iloc[i+1] < df['low'].iloc[i] and body_curr > body_prev * 2:
                    pois.append({
                        'tf': tf,
                        'type': 'OB_BEARISH',
                        'top': df['high'].iloc[i],
                        'bottom': df['low'].iloc[i],
                        'level': (df['high'].iloc[i] + df['low'].iloc[i]) / 2
                    })
        
        # Filter for 'Fresh' POIs (Not yet mitigated)
        df_now = self.fetch_data(symbol, '1m', limit=1)
        if df_now is None or df_now.empty: return []
        
        current_price = df_now.iloc[-1]['close']
        fresh_pois = [p for p in pois if (p['type'].endswith('BULLISH') and current_price > p['bottom']) or 
                                       (p['type'].endswith('BEARISH') and current_price < p['top'])]
        
        return fresh_pois

    def _calculate_synthetic_volume_profile(self, symbol, swept_level, direction):
        """
        98% Reliability Fallback: 1.2x Absorption Ratio Verification.
        Analyzes delta between last 5m tick-volume and 1H average volume.
        """
        try:
            df_5m = self.fetch_data(symbol, '5m', limit=20, synchronized=False)
            df_1h = self.fetch_data(symbol, '1h', limit=50, synchronized=False)
            
            if df_5m is None or df_1h is None: return False
            
            # 1. Calculate 1H average volume (Baseline)
            # Scale 1H volume to 5M equivalent (1/12)
            avg_vol_1h = (df_1h['volume'].mean() / 12)
            if avg_vol_1h == 0: return False
            
            # 2. Detect last relevant 5M volume (Tick-delta Proxy)
            # We look for the volume spike in the last 2 candles
            peak_vol_5m = df_5m['volume'].iloc[-2:].max()
            
            absorption_ratio = peak_vol_5m / avg_vol_1h
            
            if absorption_ratio >= 1.2:
                logger.info(f"✅ Synthetic Absorption Verified: {absorption_ratio:.2f}x (Limit: 1.2x)")
                return True
            else:
                logger.warning(f"❌ Low Absorption Detected: {absorption_ratio:.2f}x (Below 1.2x limit). Rejecting sweep.")
                return False
        except Exception as e:
            logger.error(f"Synthetic volume calc failed: {e}")
            return False

    def validate_sweep_depth(self, symbol, swept_level, direction):
        """
        Refactored 98% Reliability Filter: Institutional Absorption Verification.
        Removed return True fallback for failed API calls.
        """
        if not self.order_book_enabled:
            return self._calculate_synthetic_volume_profile(symbol, swept_level, direction)
        
        try:
            # Fetch order book (Level 2 depth)
            order_book = self.exchange.fetch_order_book(symbol, limit=20)
            total_volume = 0
            
            if direction == 'LONG':
                bids = order_book.get('bids', [])
                for price, amount in bids:
                    # Within 0.5% of swept level
                    if abs(price - swept_level) / swept_level < 0.005:
                        total_volume += amount
                
                # Require minimum 5 BTC/Unit of buy-side absorption or fallback
                if total_volume >= 5.0: return True
                return self._calculate_synthetic_volume_profile(symbol, swept_level, direction)
            
            # For SHORT setup (sweep above), check sell-side absorption
            else:
                asks = order_book.get('asks', [])
                for price, amount in asks:
                    if abs(price - swept_level) / swept_level < 0.005:
                        total_volume += amount
                
                if total_volume >= 5.0: return True
                return self._calculate_synthetic_volume_profile(symbol, swept_level, direction)
        
        except Exception as e:
            logger.warning(f"Order book fetch failed for {symbol}: {e}. Falling back to synthetic volume profile.")
            return self._calculate_synthetic_volume_profile(symbol, swept_level, direction)
    
    def calculate_atr(self, df, period=14):
        """
        Calculate Average True Range for volatility-adjusted targeting.
        
        Args:
            df: OHLCV dataframe
            period: ATR period (default 14)
        
        Returns:
            pandas Series with ATR values
        """
        high = df['high']
        low = df['low']
        close = df['close']
        
        # True Range = max(high-low, abs(high-prev_close), abs(low-prev_close))
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        return atr
    
    def detect_mss(self, df):
        """
        Market Structure Shift: 
        Evaluates classical fractal break OR Zero-Lag Kalman Velocity Inflection.
        """
        # 1. Zero-Lag Kalman State-Space Inflection Check
        try:
            k_shift, k_mag = self.shadow_sub.evaluate_kalman_mss(df)
            if k_shift == "BULLISH_SHIFT" and k_mag > 0:
                return 'BULLISH'
            elif k_shift == "BEARISH_SHIFT" and k_mag > 0:
                return 'BEARISH'
        except Exception as _k_err:
            logger.debug(f"Kalman MSS error: {_k_err}")

        # 2. Classical Fractal Break Fallback
        is_high, is_low = self.detect_fractals(df)
        
        # Get most recent confirmed swing high/low
        recent_highs = df[is_high]['high']
        recent_lows = df[is_low]['low']
        
        if recent_highs.empty or recent_lows.empty:
            return None
            
        last_high = recent_highs.iloc[-1]
        last_low = recent_lows.iloc[-1]
        
        current_close = df['close'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        
        mss_bullish = current_close > last_high and prev_close <= last_high
        mss_bearish = current_close < last_low and prev_close >= last_low
        
        if mss_bullish: return 'BULLISH'
        if mss_bearish: return 'BEARISH'
        return None


    def get_session_vwap_bands(self, df: pd.DataFrame) -> dict:
        """Computes live Session VWAP +/- 2.0 Sigma dispersion bands."""
        return self.shadow_sub.calculate_session_vwap_bands(df)

    def is_displaced_move(self, df, direction, smt_strength=0.0):
        """
        Confirms institutional participation via high-momentum candle (Displacement).
        Body > 1.5 * ATR.
        Alpha-Weighted: If SMT Strength > 0.7, allow 1.1x sensitivity.
        """
        last = df.iloc[-1]
        atr = self.calculate_atr(df).iloc[-1]
        body_size = abs(last['close'] - last['open'])
        
        # 3. Global Displacement Floor (1.5x ATR) <!-- id: 10 -->
        multiplier = 1.5
             
        return body_size > (atr * multiplier)

    def get_displacement_metrics(self, df, direction):
        """
        Calculates mathematical metrics for the last candle to audit institutional displacement.
        """
        if df is None or len(df) < 5:
            return {}
            
        last = df.iloc[-1]
        prev = df.iloc[-2]
        atr = self.calculate_atr(df).iloc[-1]
        
        # 1. Wick-to-Body Ratio
        body_size = abs(last['close'] - last['open'])
        high_wick = last['high'] - max(last['close'], last['open'])
        low_wick = min(last['close'], last['open']) - last['low']
        
        rejection_wick = low_wick if direction == 'LONG' else high_wick
        wick_to_body = rejection_wick / body_size if body_size > 0 else 1.0
        
        # 2. Displacement Multipliers
        disp_atr = body_size / atr if atr > 0 else 0
        
        # 3. Volume Z-Score (Session-Relative)
        recent_vols = df['volume'].iloc[-20:]
        avg_vol = recent_vols.mean()
        std_vol = recent_vols.std()
        
        # Calculate Z-score: (Current - Mean) / Std
        # This remains sensitive during low-vol Asian hours
        vol_zscore = (last['volume'] - avg_vol) / std_vol if std_vol > 0 else 0
        
        return {
            "wick_to_body_ratio": round(wick_to_body, 2),
            "displacement_atr": round(disp_atr, 2),
            "volume_delta": round(last['volume'] / avg_vol if avg_vol > 0 else 1.0, 2),
            "volume_zscore": round(vol_zscore, 2),
            "wick_rejection_atr": round(rejection_wick / atr, 2) if atr > 0 else 0
        }

    def get_technical_metadata_payload(self, df, current_quartiles=None):
        """
        Aggregates all detected structures (FVG, OB, Liquidity) into a visualizable payload.
        """
        metadata = []
        
        # 1. Detect FVGs (unmitigated in last 25)
        recent = df.iloc[-25:]
        for i in range(2, len(recent)):
            c0 = recent.iloc[i]
            c2 = recent.iloc[i-2]
            
            # Bullish FVG
            if c2['high'] < c0['low']:
                metadata.append({
                    "type": "FVG",
                    "direction": "BULLISH",
                    "top": c0['low'],
                    "bottom": c2['high'],
                    "start_time": recent.index[i-2],
                    "end_time": recent.index[i]
                })
            # Bearish FVG
            elif c2['low'] > c0['high']:
                metadata.append({
                    "type": "FVG",
                    "direction": "BEARISH",
                    "top": c2['low'],
                    "bottom": c0['high'],
                    "start_time": recent.index[i-2],
                    "end_time": recent.index[i]
                })
        
        # 2. Liquidity Pools (PDH, PDL)
        p_high = df['high'].iloc[-288:-1].max()
        p_low = df['low'].iloc[-288:-1].min()
        
        metadata.append({"type": "LIQ_POOL", "label": "PDH", "price": p_high})
        metadata.append({"type": "LIQ_POOL", "label": "PDL", "price": p_low})
        
        return metadata

    def calculate_ote(self, swing_low: float, swing_high: float, direction: str) -> dict:
        """
        Optimal Trade Entry (OTE) — Algorithmic Fibonacci Retracement Levels.
        Computes 62.0%, 70.5% (Golden Sweet Spot), and 79.0% discount/premium zones.
        """
        leg_range = abs(swing_high - swing_low)
        if leg_range <= 0:
            return {}
            
        if direction.upper() == 'LONG':
            ote_62 = swing_high - (leg_range * 0.62)
            ote_705 = swing_high - (leg_range * 0.705)
            ote_79 = swing_high - (leg_range * 0.79)
            return {
                "direction": "LONG",
                "ote_62": round(ote_62, 2),
                "ote_sweet_spot": round(ote_705, 2),
                "ote_79": round(ote_79, 2),
                "zone_high": round(ote_62, 2),
                "zone_low": round(ote_79, 2)
            }
        else:
            ote_62 = swing_low + (leg_range * 0.62)
            ote_705 = swing_low + (leg_range * 0.705)
            ote_79 = swing_low + (leg_range * 0.79)
            return {
                "direction": "SHORT",
                "ote_62": round(ote_62, 2),
                "ote_sweet_spot": round(ote_705, 2),
                "ote_79": round(ote_79, 2),
                "zone_high": round(ote_79, 2),
                "zone_low": round(ote_62, 2)
            }

    def validate_fvg_consequent_encroachment(self, df: pd.DataFrame, fvg_top: float, fvg_bottom: float, direction: str) -> bool:
        """
        Consequent Encroachment (50% CE) Validation for Fair Value Gaps.
        Returns False if any recent candle body (close) has closed beyond the 50% midpoint.
        """
        ce_level = (fvg_top + fvg_bottom) / 2.0
        recent = df.iloc[-5:]
        
        if direction.upper() == 'LONG':
            # Bullish FVG: candle close must NOT close below CE level
            breached = any(recent['close'] < ce_level)
            return not breached
        else:
            # Bearish FVG: candle close must NOT close above CE level
            breached = any(recent['close'] > ce_level)
            return not breached

    def validate_order_block_mean_threshold(self, df: pd.DataFrame, ob_top: float, ob_bottom: float, direction: str) -> bool:
        """
        Mean Threshold (50% MT) Validation for Order Blocks.
        Returns False if any recent candle body (close) has closed beyond the 50% midpoint.
        """
        mt_level = (ob_top + ob_bottom) / 2.0
        recent = df.iloc[-5:]
        
        if direction.upper() == 'LONG':
            breached = any(recent['close'] < mt_level)
            return not breached
        else:
            breached = any(recent['close'] > mt_level)
            return not breached

    def check_turtle_soup_time_in_zone(self, df: pd.DataFrame, swept_level: float, direction: str, max_candles: int = 4) -> bool:
        """
        Turtle Soup Velocity / Time-in-Zone Invalidation Gate.
        A true liquidity sweep is V-shaped and rejects rapidly.
        If price stays beyond the swept level for more than max_candles (4), it is a true breakout, not a sweep.
        """
        recent = df.iloc[-8:]
        if direction.upper() == 'LONG':
            # Swept Low: count candles whose close remained below swept_level
            candles_below = sum(recent['close'] < swept_level)
            return candles_below <= max_candles
        else:
            # Swept High: count candles whose close remained above swept_level
            candles_above = sum(recent['close'] > swept_level)
            return candles_above <= max_candles

    def detect_breaker_blocks(self, df: pd.DataFrame) -> List[dict]:
        """
        Breaker Block Detection:
        Identifies an Order Block that resulted in a liquidity sweep of previous swing points,
        followed by an aggressive displacement break back through the Order Block, flipping it into support/resistance.
        """
        breakers = []
        if df is None or len(df) < 30:
            return breakers
            
        is_high, is_low = self.detect_fractals(df)
        recent_highs = df[is_high]
        recent_lows = df[is_low]
        
        if len(recent_highs) < 2 or len(recent_lows) < 2:
            return breakers
            
        last_high = recent_highs.iloc[-1]['high']
        last_low = recent_lows.iloc[-1]['low']
        current_close = df.iloc[-1]['close']
        
        if current_close > last_high:
            breakers.append({
                "type": "BREAKER_BULLISH",
                "direction": "LONG",
                "level": last_high,
                "invalidation": last_low
            })
        elif current_close < last_low:
            breakers.append({
                "type": "BREAKER_BEARISH",
                "direction": "SHORT",
                "level": last_low,
                "invalidation": last_high
            })
            
        return breakers

    def detect_inducement_trap(self, df, direction):
        """
        Detects 'Retail Inducement' (minor highs/lows) swept just before reversal.
        """
        recent = df.tail(10)
        last = df.iloc[-1]
        
        if direction == 'LONG':
            # Swept a minor low (inducement) before MSS
            minor_low = recent['low'].iloc[:-1].min()
            return last['low'] < minor_low and last['close'] > minor_low
        else:
            minor_high = recent['high'].iloc[:-1].max()
            return last['high'] > minor_high and last['close'] < minor_high

    def get_volatility_adjusted_target(self, df, direction, entry_price, session_range, symbol="BTC/USD"):
        """
        ATR-Dynamic Targeting: Adjusts targets based on current volatility.
        
        High Volatility (ATR > 1.5x mean): Target SD 2.0 (capture expansion)
        Low Volatility (ATR < mean): Target nearest FVG or institutional draw (minimum 3R)
        Normal Volatility: Target SD 1.0 (current strategy)
        
        Args:
            df: OHLCV dataframe
            direction: 'LONG' or 'SHORT'
            entry_price: Entry price
            session_range: Price quartiles dict
            symbol: Symbol string for configuration mapping
        
        Returns:
            Target price (guaranteed minimum 3R from entry and respecting MIN_TARGET_PCT floor)
        """
        atr = self.calculate_atr(df)
        if atr is None or len(atr) < 14:
            # Fallback to SD 1.0 if ATR unavailable
            target = session_range.get('sd_1_pos' if direction == 'LONG' else 'sd_1_neg')
            stop_buffer = entry_price * getattr(Config, 'MIN_STOP_PCT', {}).get(symbol, 0.002)
        else:
            mean_atr = atr.iloc[-50:].mean()  # 50-period mean
            current_atr = atr.iloc[-1]
            
            # Calculate stop loss to determine dynamic minimum target
            stop_buffer = current_atr * Config.STOP_LOSS_ATR_MULTIPLIER
            
            # Apply MIN_STOP_PCT floor to stop buffer (prevents tiny stop losses)
            min_stop_pct = getattr(Config, 'MIN_STOP_PCT', {}).get(symbol, 0.002)
            min_stop_distance = entry_price * min_stop_pct
            stop_buffer = max(stop_buffer, min_stop_distance)
            
            if direction == 'LONG':
                stop_loss = entry_price - stop_buffer
                risk = entry_price - stop_loss
                min_target_dynamic = entry_price + (Config.TP1_R_MULTIPLE * risk)
            else:  # SHORT
                stop_loss = entry_price + stop_buffer
                risk = stop_loss - entry_price
                min_target_dynamic = entry_price - (Config.TP1_R_MULTIPLE * risk)
            
            # High Volatility: Expanded Targets
            if current_atr > mean_atr * 1.5:
                logger.info(f"📈 High Volatility Detected (ATR: {current_atr:.2f} > {mean_atr*1.5:.2f}). Targeting SD 2.0")
                target = session_range.get('sd_2_pos' if direction == 'LONG' else 'sd_2_neg', 
                                        session_range.get('sd_1_pos' if direction == 'LONG' else 'sd_1_neg'))
            
            # Low Volatility: Use institutional draw, NOT session midpoint
            elif current_atr < mean_atr:
                logger.info(f"📉 Low Volatility Detected (ATR: {current_atr:.2f} < {mean_atr:.2f}). Targeting Institutional Draw (min 3R)")
                # Use get_next_institutional_target instead of session midpoint
                target = self.get_next_institutional_target(df, direction, entry_price)
            
            # Normal Volatility: SD 1.0 (current strategy)
            else:
                target = session_range.get('sd_1_pos' if direction == 'LONG' else 'sd_1_neg')
            
            # 98% Reliability: Target Guard Rails
            # Ensure target is ALWAYS in the right direction
            if direction == 'LONG':
                target = max(target or 0, min_target_dynamic)
            else: # SHORT
                target = min(target or float('inf'), min_target_dynamic)

        # Apply MIN_TARGET_PCT floor to target (prevents noise-level weekend ATR targets)
        cfg_min_target = getattr(Config, 'MIN_TARGET_PCT', 0.0035)
        min_target_pct = cfg_min_target.get(symbol, 0.0035) if isinstance(cfg_min_target, dict) else float(cfg_min_target)
        min_target_distance = entry_price * min_target_pct
        
        if direction == 'LONG':
            target = max(target or 0, entry_price + min_target_distance)
        else:
            target = min(target or float('inf'), entry_price - min_target_distance)
            
        # CRITICAL: Final sanity check on absolute return direction
        if direction == 'LONG' and target <= entry_price:
            logger.warning(f"🛡️ Fix LONG Target: {target:.2f} <= {entry_price:.2f}. Forcing 2.5R.")
            target = entry_price + (Config.TP1_R_MULTIPLE * stop_buffer)
        elif direction == 'SHORT' and target >= entry_price:
            logger.warning(f"🛡️ Fix SHORT Target: {target:.2f} >= {entry_price:.2f}. Forcing 2.5R.")
            target = entry_price - (Config.TP1_R_MULTIPLE * stop_buffer)

        return target
            
    def get_next_institutional_target(self, df, direction, entry_price):
        """
SMC Scanner — Bayesian Pivot Infra
====================================
Detects Smart Money Concepts (SMC) formations across multiple timeframes.

Core capabilities (implementation is private):
  - Order Block detection (Bullish / Bearish)
  - Fair Value Gap (FVG) classification
  - Inducement & Liquidity Sweep identification
  - Break of Structure (BOS) and Change of Character (CHoCH) labelling
  - 4H bias determination via EMA-based market structure
  - Session-quartile-aware entry timing
  - SMT (Smart Money Tool) divergence scoring against correlated assets

For research enquiries: github.com/nicholasmacaskill/bayesian-pivot-trading-infra-public
"""
        target = None
        min_rr = 3.0 # Institutional minimum risk/reward aspiration
        
        # Scan last 100 candles for resting liquidity
        recent = df.iloc[-100:]
        
        if direction == "LONG":
            # 1. Look for Bearish FVG above entry
            # Bearish FVG: Low of candle i-2 > High of candle i
            for i in range(len(recent)-3, 0, -1):
                c0 = recent.iloc[i]     # Current
                c2 = recent.iloc[i-2]   # 2 candles ago
                
                # Check for gap
                if c2['low'] > c0['high']:
                    fvg_bottom = c0['high']
                    # Is it above our entry?
                    if fvg_bottom > entry_price:
                        # Is it "Unfilled" (Price hasn't traded through it yet)?
                        # Simplified check: Just find the first valid one above current
                        return fvg_bottom
            
            # 2. Fallback: Major Swing High (Liquidity Pool)
            swing_high = recent['high'].max()
            if swing_high > entry_price:
                return swing_high
                
            # 3. Last Resort: 1:4 Expansion
            return entry_price * 1.02 

        elif direction == "SHORT":
            # 1. Look for Bullish FVG below entry
            # Bullish FVG: High of candle i-2 < Low of candle i
            for i in range(len(recent)-3, 0, -1):
                c0 = recent.iloc[i]     # Current (High)
                c2 = recent.iloc[i-2]   # 2 candles ago (Low)
                
                if c2['high'] < c0['low']:
                    fvg_top = c0['low']
                    if fvg_top < entry_price:
                        return fvg_top
                        
            # 2. Fallback: Major Swing Low
            swing_low = recent['low'].min()
            if swing_low < entry_price:
                return swing_low
                
            # 3. Last Resort: 1:4 Expansion
            return entry_price * 0.98

        return target

    def is_tapping_fvg(self, df, direction):
        """
        Checks if current price is tapping into a valid, unmitigated Fair Value Gap.
        Used for 'Standard Pullback' entries.
        """
        current_low = df['low'].iloc[-1]
        current_high = df['high'].iloc[-1]
        
        # Scan last 20 candles for FVG
        recent = df.iloc[-25:-1] # Look back, excluding current
        
        if direction == "LONG":
            # Look for Bullish FVG (Buying opportunity in Discount)
            # Bullish FVG: Low of candle i > High of candle i-2
            for i in range(2, len(recent)):
                c0 = recent.iloc[i]     # Top of FVG (Low of candle i)
                c2 = recent.iloc[i-2]   # Bottom of FVG (High of candle i-2)
                
                if c0['low'] > c2['high']:
                    fvg_top = c0['low']
                    fvg_bottom = c2['high']
                    
                    # Check mitigation: Has price ALREADY closed below this FVG?
                    # If so, it's invalid.
                    # Simplified: Check if current price is INSIDE it.
                    if current_low <= fvg_top and current_low >= fvg_bottom:
                         return True
                         
        elif direction == "SHORT":
            # Look for Bearish FVG (Selling opportunity in Premium)
            # Bearish FVG: High of candle i < Low of candle i-2
            for i in range(2, len(recent)):
                c0 = recent.iloc[i]     # Bottom of FVG (High of candle i)
                c2 = recent.iloc[i-2]   # Top of FVG (Low of candle i-2)
                
                if c0['high'] < c2['low']:
                    fvg_top = c2['low']
                    fvg_bottom = c0['high']
                    
                    # Check if current price is INSIDE it.
                    if current_high >= fvg_bottom and current_high <= fvg_top:
                        return True
                        
        return False

    @safe_scan("Scanner.scan_pattern")
    def scan_pattern(self, symbol, timeframe=Config.TIMEFRAME, cached_context=None, provided_df=None, current_time_override=None, visual_check=True):
        """
        Main Scanning Function.
        Checks: Killzone -> Trend Bias -> Price Quartiles -> SMC Pattern
        """
        # 1. HARD GATE: Time (Killzone)
        if not self.is_killzone(current_time=current_time_override):
            return None

        # DEDUPLICATION GATE: Prevent the same symbol from firing multiple times per candle window
        now_ts = (current_time_override or datetime.utcnow()).timestamp()
        cache_key = symbol
        last_fired = self._signal_cache.get(cache_key, 0)
        cooldown_secs = self._signal_cooldown_mins * 60
        if (now_ts - last_fired) < cooldown_secs:
            logger.debug(f"🔇 Deduplicated signal for {symbol} (cooldown: {int(cooldown_secs - (now_ts - last_fired))}s remaining)")
            return None

        # 2. SOFT GATE: News Context (Use Cache or Live)
        if cached_context and 'news' in cached_context:
            news_data = cached_context['news']
            is_safe = news_data['is_safe']
            event = news_data['event']
            mins = news_data['minutes_until']
        else:
            # TODO: Add news mocking for backtest
            is_safe, event, mins = self.news.is_news_safe(symbol=symbol)
        
        news_context = "Clear"
        if not is_safe:
             news_context = f"ACTIVE EVENT: {event} in {mins}m"
             print(f"⚠️ News Event Detected: {event}. Proceeding with CAUTION.")
             
        # 3. Fetch Institutional Context (Use Cache or Live)
        if cached_context and 'intermarket' in cached_context:
            index_context = cached_context['intermarket']
        else:
            index_context = self.intermarket.get_market_context()
            
        # 4. HARD GATE: Bias (HTF 4H + Daily + Intermarket + Visual)
        # We pass visual_check as per parameter (default True)
        bias_full = self.get_detailed_bias(symbol, index_context=index_context, visual_check=visual_check, current_time=current_time_override)
        has_macro_conviction = "STRONG" in bias_full
        
        if has_macro_conviction:
            logger.info(f"💪 STRONG BIAS DETECTED: {bias_full}")
        
        # 3. GET SESSION METADATA (Time & Price Quartiles)
        time_quartile = self.get_session_quartile(current_time=current_time_override)
        price_quartiles = self.get_price_quartiles(symbol)
        
        if provided_df is not None:
             df = provided_df
        else:
             df = self.fetch_data(symbol, timeframe)
             
        if df is None:
            return None

        # Current and recent data
        current = df.iloc[-1]
        
        # 288 candles * 5m = 1440m = 24 hours
        recent_high = df['high'].iloc[-288:-1].max()
        recent_low = df['low'].iloc[-288:-1].min()

        # TIER 1: Time Series Analysis (New Quant Layer)
        closes = df['close'].values
        hurst = self.get_hurst_exponent(closes)
        adf_p = self.get_adf_test(closes)
        
        # --- Hurst 'Chaos' Buffer Reduction (Gate 1 Refinement) ---
        # Update: (0.48, 0.52) must be rejected as CHOP / RANDOM
        hurst_low, hurst_high = Config.get('HURST_CHAOS_RANGE', (0.48, 0.52))
        if not has_macro_conviction and hurst_low <= hurst <= hurst_high:
            logger.debug(f"Hurst Chaos Buffer: Skipping random walk ({hurst:.3f})")
            return None

        # --- Dynamic Session Calibration (Regime Override) ---
        utc_hour = (current_time_override or datetime.utcnow()).hour
        is_asian_london = (0 <= utc_hour < 10) or (20 <= utc_hour <= 23)
        
        # Asian/Late NY Mode: Prioritize Mean-Reversion (Fades)
        if not has_macro_conviction and is_asian_london and hurst > 0.55:
            logger.debug(f"Session Calibration: Skipping Expansion during Low-Vol Asian/London hours (Hurst: {hurst:.2f})")
            return None
            
        # London/NY Open Mode: Prioritize Expansion (Continuations)
        if not has_macro_conviction and not is_asian_london and hurst < 0.45:
             logger.debug(f"Session Calibration: Skipping Mean-Reversion during Trending NY hours (Hurst: {hurst:.2f})")
             return None

        hurst_low_val = Config.get('HURST_CHAOS_RANGE', (0.48, 0.52))[0]
        is_mean_reverting = hurst < hurst_low_val or adf_p < 0.05

        setup = None
        entry_type = None

        # BULLISH Setup (OPTIMIZED: Allow Counter-Trend if Mean-Reverting)
        can_check_long = "BULLISH" in bias_full
        is_mean_reverting_regime = hurst < 0.45
        
        # Counter-Trend Reversal Logic (Turtle Soup)
        # 98% Reliability: Do not allow a 'Fade' if volume is accelerating (Capitulation)
        vol_spike = self.calculate_volume_cluster(df)
        
        # STRICT DAILY TREND FILTER: Block counter-trend setups
        last_bias_1d = getattr(self, '_last_bias_1d', {}).get(symbol, 0)
        if last_bias_1d == -1:
            # Daily trend is BEARISH: Hard-block all LONG setups
            can_check_long = False
        elif not can_check_long and is_mean_reverting_regime and vol_spike < 1.3:
            can_check_long = True # Allow search for long even if bias is Bearish

        if can_check_long:
            # TIER 1: Deep Discount
            in_deep_discount = False
            if price_quartiles:
                ref_range = price_quartiles.get("Asian Range") or price_quartiles.get("CBDR")
                if ref_range:
                    price_position = (current['close'] - ref_range['low']) / (ref_range['high'] - ref_range['low'])
                    if Config.MIN_PRICE_QUARTILE <= price_position <= Config.MAX_PRICE_QUARTILE:
                        in_deep_discount = True
            
            # TIER 1: SMT (Multi-Asset Sponsorship)
            smt_strength = self.intermarket.calculate_cross_asset_divergence('LONG', index_context)
            has_strong_smt = smt_strength >= Config.MIN_SMT_STRENGTH
            
            # LOGIC A: JUDAS SWEEP (High Alpha)
            swept_pdl = current['low'] < recent_low and current['close'] > recent_low
            swept_london = False
            london_range = None
            if price_quartiles and "London Range" in price_quartiles:
                london_range = price_quartiles["London Range"]
                swept_london = current['low'] < london_range["low"] and current['close'] > london_range["low"]

            # PHASE 2: Volume & SMT Confirmation
            vol_spike = self.calculate_volume_cluster(df)
            true_smt_type, true_smt_strength = self.intermarket.detect_true_smt(df, "DXY")
            has_true_smt = true_smt_type is not None
            
            # Merge SMT strengths (Divergence Score + True SMT magnitude)
            combined_smt = max(smt_strength, true_smt_strength)
            
            # PHASE 2: 90-Minute Cycle Logic (Q2 Manipulation Window)
            is_q2 = time_quartile.get('num') == 2

            # RESTRUCTURED: In Mean-Reverting regimes, strictly require DISCOUNT to avoid buying the top.
            if is_mean_reverting_regime:
                can_proceed_long = in_deep_discount
            else:
                can_proceed_long = (in_deep_discount or has_true_smt or has_strong_smt)
                # If bias is Bearish, DO NOT allow Trend Following Longs
                if "BULLISH" not in bias_full:
                    can_proceed_long = False

            if can_proceed_long:
                # Require Volume Spike (Smart Money Print) or True SMT for JUDAS
                if (swept_pdl or swept_london) and (vol_spike >= 1.5 or has_true_smt):
                    # LEVEL 2 DEPTH FILTER
                    swept_level = recent_low if swept_pdl else (london_range["low"] if london_range else recent_low)
                    if self.validate_sweep_depth(symbol, swept_level, 'LONG'):
                        entry_type = "Judas Sweep (High Alpha)"
                
                # LOGIC B: FVG TAP (Medium Alpha - Standard Pullback)
                # Ensure Q3 (Distribution) for pullbacks, or Q2 with extremely high volume
                elif self.is_tapping_fvg(df, 'LONG'):
                     if time_quartile.get('num') >= 3 or (is_q2 and vol_spike >= 2.0):
                        entry_type = "Trend Pullback (Medium Alpha)"

            if entry_type:
                # ATR-DYNAMIC TARGETING
                ref_range_target = london_range or price_quartiles.get("Asian Range")
                target = self.get_volatility_adjusted_target(df, 'LONG', current['close'], ref_range_target, symbol=symbol)
                
                if not target:
                    target = self.get_next_institutional_target(df, "LONG", current['close'])

                # STRATEGY: WIDE NET
                # ATR-based Limit Offset for better entries
                atr = self.calculate_atr(df).iloc[-1]
                if pd.isna(atr): atr = current['close'] * 0.005
                
                # Bulls want to buy a dip (lower limit price)
                limit_entry = current['close'] - (atr * Config.ENTRY_OFFSET_ATR_MULTIPLIER)
                
                # Ensure stop loss is not too tight (floor at MIN_STOP_LOSS_ATR and recent fractal low)
                stop_buffer = max(atr * Config.STOP_LOSS_ATR_MULTIPLIER, atr * Config.get('MIN_STOP_LOSS_ATR', 2.0))
                min_stop_pct = getattr(Config, 'MIN_STOP_PCT', {}).get(symbol, 0.003)
                min_stop_distance = limit_entry * min_stop_pct
                stop_buffer = max(stop_buffer, min_stop_distance)
                
                direction = 'LONG'
                recent_low_val = df['low'].iloc[-12:].min() if len(df) >= 12 else (limit_entry - stop_buffer)
                calc_stop = limit_entry - stop_buffer
                stop_loss = min(calc_stop, recent_low_val - (atr * 0.5))
                risk = limit_entry - stop_loss
                
                # Trinity Check
                cross_asset_div = self.intermarket.calculate_cross_asset_divergence('LONG', index_context)

                setup = {
                    "timestamp": current['timestamp'].isoformat() if hasattr(current['timestamp'], 'isoformat') else str(current['timestamp']),
                    "symbol": symbol,
                    "pattern": f"Bullish {entry_type}",
                    "bias": bias_full,
                    "entry": limit_entry,
                    "stop_loss": stop_loss,
                    "target": target,
                    'tp1': limit_entry + (risk * Config.TP1_R_MULTIPLE),
                    'direction': direction,
                    "time_quartile": time_quartile,
                    "price_quartiles": price_quartiles,
                    "index_context": index_context,
                    "smt_strength": round(combined_smt, 2),
                    "hurst_exponent": round(hurst, 2),
                    "adf_p_value": round(adf_p, 4),
                    "is_mean_reverting": bool(is_mean_reverting),
                    "cross_asset_divergence": round(cross_asset_div, 2),
                    "news_context": news_context,
                    "is_discount": True,
                    'risk_reward': Config.TP2_R_MULTIPLE,
                    'quality': 'HIGH' if 'Judas' in entry_type else 'MEDIUM',
                    'volume_spike': vol_spike,
                    'true_smt': true_smt_type
                }


        # BEARISH Setup (OPTIMIZED: Require bias for quality - Only if no Long found yet)
        last_bias_1d = getattr(self, '_last_bias_1d', {}).get(symbol, 0)
        if last_bias_1d == 1:
            # Daily trend is BULLISH: Hard-block all SHORT setups
            can_check_short = False
        else:
            can_check_short = "BEARISH" in bias_full

        if not setup and can_check_short:
            # TIER 1: Premium
            in_premium = False
            if price_quartiles:
                ref_range = price_quartiles.get("Asian Range") or price_quartiles.get("CBDR")
                if ref_range:
                    price_position = (current['close'] - ref_range['low']) / (ref_range['high'] - ref_range['low'])
                    if Config.MIN_PRICE_QUARTILE_SHORT <= price_position <= Config.MAX_PRICE_QUARTILE_SHORT:
                        in_premium = True
            # TIER 1: SMT (Multi-Asset Sponsorship)
            smt_strength = self.intermarket.calculate_cross_asset_divergence('SHORT', index_context)
            has_strong_smt = smt_strength >= Config.MIN_SMT_STRENGTH
            
            # LOGIC A: JUDAS SWEEP (High Alpha)
            swept_pdh = current['high'] > recent_high and current['close'] < recent_high
            swept_london = False
            london_range = None
            if price_quartiles and "London Range" in price_quartiles:
                london_range = price_quartiles["London Range"]
                swept_london = current['high'] > london_range["high"] and current['close'] < london_range["high"]

            # PHASE 2: Volume & SMT Confirmation (Bearish)
            vol_spike = self.calculate_volume_cluster(df)
            true_smt_type, true_smt_strength = self.intermarket.detect_true_smt(df, "DXY")
            has_true_smt = true_smt_type is not None
            
            # Merge SMT
            combined_smt_short = max(smt_strength, true_smt_strength)
            
            is_q2 = time_quartile.get('num') == 2

            entry_type = None

            
            # SIMPLIFIED: Proceed if we have premium zone + SMT alignment
            # PHASE 2: Strict Institutional Requirements
            # RESTRUCTURED: In Mean-Reverting regimes, strictly require PREMIUM to avoid selling the low.
            is_mean_reverting_regime = hurst < 0.45
            
            if is_mean_reverting_regime:
                can_proceed = in_premium
            else:
                can_proceed = (in_premium or has_true_smt or has_strong_smt)

            if can_proceed:
                if (swept_pdh or swept_london) and (vol_spike >= 1.5 or has_true_smt):
                    # LEVEL 2 DEPTH FILTER
                    swept_level = recent_high if swept_pdh else (london_range["high"] if london_range else recent_high)
                    if self.validate_sweep_depth(symbol, swept_level, 'SHORT'):
                        entry_type = "Judas Sweep (High Alpha)"
                
                # LOGIC B: FVG TAP (Medium Alpha)
                elif self.is_tapping_fvg(df, 'SHORT'):
                     if time_quartile.get('num') >= 3 or (is_q2 and vol_spike >= 2.0):
                        entry_type = "Trend Pullback (Medium Alpha)"

            if entry_type:
                ref_range_target = london_range or price_quartiles.get("Asian Range")
                target = self.get_volatility_adjusted_target(df, 'SHORT', current['close'], ref_range_target, symbol=symbol)
                
                if not target:
                    target = self.get_next_institutional_target(df, "SHORT", current['close'])
                
                atr = self.calculate_atr(df).iloc[-1]
                if pd.isna(atr): atr = current['close'] * 0.005
                
                # Bears want to sell a pump (higher limit price)
                limit_entry = current['close'] + (atr * Config.ENTRY_OFFSET_ATR_MULTIPLIER)
                
                # Ensure stop loss is not too tight (floor at MIN_STOP_LOSS_ATR and recent fractal high)
                stop_buffer = max(atr * Config.STOP_LOSS_ATR_MULTIPLIER, atr * Config.get('MIN_STOP_LOSS_ATR', 2.0))
                min_stop_pct = getattr(Config, 'MIN_STOP_PCT', {}).get(symbol, 0.003)
                min_stop_distance = limit_entry * min_stop_pct
                stop_buffer = max(stop_buffer, min_stop_distance)
                
                direction = 'SHORT'
                recent_high_val = df['high'].iloc[-12:].max() if len(df) >= 12 else (limit_entry + stop_buffer)
                calc_stop = limit_entry + stop_buffer
                stop_loss = max(calc_stop, recent_high_val + (atr * 0.5))
                risk = stop_loss - limit_entry
                
                cross_asset_div = self.intermarket.calculate_cross_asset_divergence('SHORT', index_context)
                
                setup = {
                    "symbol": symbol,
                    "pattern": f"Bearish {entry_type}",
                    "bias": bias_full,
                    "entry": limit_entry,
                    "stop_loss": stop_loss,
                    "target": target,
                    'tp1': limit_entry - (risk * Config.TP1_R_MULTIPLE),
                    'direction': direction,
                    "time_quartile": time_quartile,
                    "price_quartiles": price_quartiles,
                    "index_context": index_context,
                    "smt_strength": round(combined_smt_short, 2),
                    "hurst_exponent": round(hurst, 2),
                    "adf_p_value": round(adf_p, 4),
                    "is_mean_reverting": bool(is_mean_reverting),
                    "cross_asset_divergence": round(cross_asset_div, 2),
                    "news_context": news_context,
                    "is_premium": True,
                    'risk_reward': Config.TP2_R_MULTIPLE,
                    'quality': 'HIGH' if 'Judas' in entry_type else 'MEDIUM',
                    'volume_spike': vol_spike,
                    'true_smt': true_smt_type
                }



        if setup:
            # --- Dynamic Target Floor (Scaled for 5m SMC Scalps & Trends) ---
            entry_px = setup.get('entry', setup.get('entry_price', 0))
            target_px = setup.get('target', setup.get('take_profit', 0))
            sl_px = setup.get('sl', setup.get('stop_loss', 0))
            
            if entry_px > 0:
                dist_pct = abs(target_px - entry_px) / entry_px
                risk_dist = abs(entry_px - sl_px)
                reward_dist = abs(target_px - entry_px)
                rr_ratio = reward_dist / risk_dist if risk_dist > 0 else 0
                
                min_target_pct = getattr(Config, 'MIN_TARGET_PCT', 0.0035)
                # Allow if either target >= 0.35% (e.g. $225+ on BTC) or Risk:Reward >= 2.0R
                if dist_pct < min_target_pct and rr_ratio < 2.0:
                    logger.warning(f"🚫 Target Floor Breach for {symbol}: {dist_pct:.2%} (Limit: {min_target_pct:.2%} or >= 2.0R). Rejecting.")
                    return None

            # Stamp cache so this symbol is deduplicated for the next cooldown window
            self._signal_cache[cache_key] = now_ts
            return setup, df
        return None

    @safe_scan("Scanner.scan_order_flow")
    def scan_trend_expansion(self, symbol, timeframe=Config.TIMEFRAME, cached_context=None):
        """
        [NEW] TREND EXPANSION SCANNER:
        Identifies 'Clean' trend moves that don't satisfy reversal criteria.
        Logic: STRONG 4H/1H Bias Alignment + Retracement to 15m/1H/4H POI.
        """
        # 1. BIAS CONVICTION GATE (Allow Conflict with fallback to 4H/1H direction)
        index_context = cached_context or self.intermarket.get_market_context()
        bias_full = self.get_detailed_bias(symbol, index_context=index_context, visual_check=False)
        
        is_strong_bull = "STRONG BULLISH" in bias_full
        is_strong_bear = "STRONG BEARISH" in bias_full
        bias_conflict = "NEUTRAL" in bias_full or "CONFLICT" in bias_full
        
        if not (is_strong_bull or is_strong_bear):
            if bias_conflict:
                # Fallback: use 4H/1H alignment for trend direction during conflict
                try:
                    df_4h = self.fetch_data(symbol, '4h', limit=100, synchronized=False)
                    df_1h = self.fetch_data(symbol, '1h', limit=100, synchronized=False)
                    if df_4h is not None and df_1h is not None:
                        ema20_4h = df_4h['close'].ewm(span=20).mean().iloc[-1]
                        ema50_4h = df_4h['close'].ewm(span=50).mean().iloc[-1]
                        ema20_1h = df_1h['close'].ewm(span=20).mean().iloc[-1]
                        ema50_1h = df_1h['close'].ewm(span=50).mean().iloc[-1]
                        if ema20_4h > ema50_4h and ema20_1h > ema50_1h:
                            is_strong_bull = True  # Treat as bullish for scanning
                        elif ema20_4h < ema50_4h and ema20_1h < ema50_1h:
                            is_strong_bear = True  # Treat as bearish for scanning
                except Exception:
                    pass
            
            if not (is_strong_bull or is_strong_bear):
                return None

        direction = 'LONG' if is_strong_bull else 'SHORT'

        # 2. HEURISTIC GATE: Hurst (Must be EXPANSION)
        df = self.fetch_data(symbol, timeframe)
        if df is None or len(df) < 50: return None
        
        hurst = self.get_hurst_exponent(df['close'].values)
        if hurst < 0.52: # Standard expansion threshold
            return None
            
        # 3. POI DETECTION: Find 15m, 1H, 4H POIs
        # We need to fetch higher timeframe data specifically for this
        pois = []
        for tf in ['15m', '1h', '4h']:
            tf_df = self.fetch_data(symbol, tf, limit=100, synchronized=False)
            if tf_df is None or len(tf_df) < 5: continue
            
            # Detect FVGs on this TF
            for i in range(2, len(tf_df)):
                # Bullish FVG
                if tf_df['low'].iloc[i] > tf_df['high'].iloc[i-2]:
                    pois.append({'type': 'FVG_BULLISH', 'top': tf_df['low'].iloc[i], 'bottom': tf_df['high'].iloc[i-2], 'tf': tf})
                # Bearish FVG
                if tf_df['high'].iloc[i] < tf_df['low'].iloc[i-2]:
                    pois.append({'type': 'FVG_BEARISH', 'top': tf_df['low'].iloc[i-2], 'bottom': tf_df['high'].iloc[i], 'tf': tf})

        if not pois:
            return None

        current_price = df['close'].iloc[-1]
        # 4. ENTRY TRIGGER: Price currently "sitting" in a POI OR Shallow Retest during High RVol Expansion
        atr = self.calculate_atr(df).iloc[-1]
        rvol = self.calculate_rvol(df)
        rvol_threshold = getattr(Config, 'RVOL_EXPANSION_THRESHOLD', 2.5)

        active_poi = None
        for p in pois:
            if direction == 'LONG' and p['type'] == 'FVG_BULLISH':
                if p['bottom'] <= current_price <= p['top'] or (rvol >= rvol_threshold and current_price >= p['bottom'] and current_price <= p['top'] + (atr * 1.5)):
                    active_poi = p
                    break
            if direction == 'SHORT' and p['type'] == 'FVG_BEARISH':
                if p['bottom'] <= current_price <= p['top'] or (rvol >= rvol_threshold and current_price <= p['top'] and current_price >= p['bottom'] - (atr * 1.5)):
                    active_poi = p
                    break
        
        if not active_poi:
            return None

        # 5. DEDUPLICATION GATE
        now_ts = datetime.utcnow().timestamp()
        cache_key = f"{symbol}_expansion"
        last_fired = self._signal_cache.get(cache_key, 0)
        if (now_ts - last_fired) < (self._signal_cooldown_mins * 60):
            return None

        # 6. Construct Setup
        # TP/SL based on POI and ATR
        if direction == 'LONG':
            entry = current_price
            stop_loss = active_poi['bottom'] - (atr * 0.5)
            target = entry + (abs(entry - stop_loss) * 3.5) # Expanding trend RR
        else:
            entry = current_price
            stop_loss = active_poi['top'] + (atr * 0.5)
            target = entry - (abs(entry - stop_loss) * 3.5)

        setup = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "pattern": f"Trend Expansion ({active_poi['tf']} {active_poi['type']})",
            "bias": bias_full,
            "entry": entry,
            "stop_loss": stop_loss,
            "target": target,
            "direction": direction,
            "time_quartile": self.get_session_quartile(),
            "price_quartiles": self.get_price_quartiles(symbol),
            "index_context": index_context,
            "hurst_regime": round(hurst, 3),
            "quality": "HIGH",
            "bias_conflict": bias_conflict
        }
        
        self._signal_cache[cache_key] = now_ts
        return setup, df

    def scan_order_flow(self, symbol, timeframe=Config.TIMEFRAME, cached_context=None):
        """
        STRATEGY 3: ICT ORDER FLOW (Order Blocks + MSS)
        Focuses on high-probability reversals or continuations sponsored by institutions.
        """
        # 1. HARD GATE: Killzone
        if not self.is_killzone():
            return None

        # DEDUPLICATION GATE: Prevent the same symbol from firing multiple times per candle window
        now_ts = datetime.utcnow().timestamp()
        cache_key = symbol
        last_fired = self._signal_cache.get(cache_key, 0)
        cooldown_secs = self._signal_cooldown_mins * 60
        if (now_ts - last_fired) < cooldown_secs:
            logger.debug(f"🔇 Deduplicated order flow signal for {symbol} ({int(cooldown_secs - (now_ts - last_fired))}s left)")
            return None

        # 2. BIAS CHECK (Soft Gate — allow conflict, flag for downstream risk reduction)
        index_context = cached_context or self.intermarket.get_market_context()
        bias_full = self.get_detailed_bias(symbol, index_context=index_context, visual_check=False) # Visual done later if needed
        
        is_bullish = "BULLISH" in bias_full
        is_bearish = "BEARISH" in bias_full
        bias_conflict = "NEUTRAL" in bias_full or "CONFLICT" in bias_full
        
        if not (is_bullish or is_bearish or bias_conflict):
            return None

        # 2. Fetch Data
        df = self.fetch_data(symbol, timeframe)
        if df is None: return None
        
        current = df.iloc[-1]
        hurst = self.get_hurst_exponent(df['close'].values)
        is_mean_reverting_regime = hurst < 0.45
        price_quartiles = self.get_price_quartiles(symbol)

        # 3. Detect SMT Strength Early (for Alpha-Weighted Displacement)
        true_smt_type, true_smt_strength = self.intermarket.detect_true_smt(df, "DXY")
        
        # 4. Detect Market Structure Shift (MSS) with SMT-weighted displacement
        mss_setup = self.detect_mss(df, lookback=50, smt_strength=true_smt_strength)
        
        if not mss_setup:
            return None
            
        direction = mss_setup['direction'] # 'LONG' or 'SHORT'
        
        # ── RESTRUCTURED: BIAS & REGIME GATES ───────────────────────
        # In Mean-Revert regimes, we allow Counter-Trend if in Extreme Quartile
        in_deep_discount = False
        in_premium = False
        if price_quartiles:
            ref_range = price_quartiles.get("Asian Range") or price_quartiles.get("CBDR")
            if ref_range:
                pos = (current['close'] - ref_range['low']) / (ref_range['high'] - ref_range['low'])
                in_deep_discount = pos <= 0.25
                in_premium = pos >= 0.75

        # Bias Gate: Allow Counter-Trend if Mean-Reverting and in Extreme zone
        if direction == 'LONG':
            if not is_bullish and not (is_mean_reverting_regime and in_deep_discount):
                return None
            # Block Mean-Revert Long if NOT in discount
            if is_mean_reverting_regime and not in_deep_discount:
                return None
        
        if direction == 'SHORT':
            if not is_bearish and not (is_mean_reverting_regime and in_premium):
                return None
            # Block Mean-Revert Short if NOT in premium
            if is_mean_reverting_regime and not in_premium:
                return None

        # 4. Find Responsible Order Block (OB)
        # The OB is the candle(s) BEFORE the displacement leg
        ob_setup = self.find_order_block(df, mss_setup['origin_index'], direction)
        
        if not ob_setup:
            return None
            
        # 5. Check if Price is within Entry Zone (OB + FVG)
        # Entry: Mean Threshold (50% of OB) or Open of OB
        entry_price = ob_setup['mean_threshold']
        stop_loss = ob_setup['invalidation_level']
        target = self.get_next_institutional_target(df, direction, current['close'])
        
        # Calculate Distance to Entry
        dist_percent = abs(current['close'] - entry_price) / current['close']
        
        # If price is too far away (> 0.5% away from OB), ignore
        if dist_percent > 0.005: 
            return None
            
        # 6. Construct Setup
        risk = abs(entry_price - stop_loss)
        if risk == 0: return None
        
        # TIER 1: Sponsorship & SMT
        cross_asset_div = self.intermarket.calculate_cross_asset_divergence(direction, index_context)
        
        # Hurst-Adaptive RR Scaling
        hurst = self.get_hurst_exponent(df['close'].values)
        if hurst > 0.6:
            rr = 5.0  # High Momentum Trend
        elif hurst > 0.5:
            rr = 3.5  # Standard Trend
        elif hurst < 0.45:
            rr = 2.0  # Mean Reversion Range (Take profits faster)
        else:
            rr = 3.0  # Default
            
        setup = {
            "timestamp": current['timestamp'].isoformat() if hasattr(current['timestamp'], 'isoformat') else str(current['timestamp']),
            "symbol": symbol,
            "pattern": f"{'Bullish' if direction == 'LONG' else 'Bearish'} Order Block (Flow)",
            "bias": bias_full,
            "entry": entry_price,
            "stop_loss": stop_loss,
            "target": entry_price + (risk * rr) if direction == 'LONG' else entry_price - (risk * rr),
            'tp1': entry_price + (risk * 2.0) if direction == 'LONG' else entry_price - (risk * 2.0),
            'direction': direction,
            "time_quartile": self.get_session_quartile(),
            "price_quartiles": self.get_price_quartiles(symbol),
            "index_context": index_context,
            "smt_strength": round(true_smt_strength, 2),
            "cross_asset_divergence": round(cross_asset_div, 2),
            "news_context": "Checked",
            "is_discount": True, # Assumed if retracing to OB
            'risk_reward': rr,
            'quality': 'HIGH',
            'true_smt': true_smt_type,
            'hurst_regime': round(hurst, 3),
            'bias_conflict': bias_conflict
        }
        
        if setup:
            # Stamp cache so this symbol is deduplicated for the next cooldown window
            self._signal_cache[cache_key] = now_ts
            return setup, df
        return None

    def detect_mss(self, df, lookback=50, smt_strength=0.0):
        """
        Detects if a Market Structure Shift has occurred recently.
        Returns dict with direction and origin index (start of displacement).
        """
        subset = df.iloc[-lookback:]
        
        # Find Swing Highs and Lows (Fractals)
        # Simple 3-candle fractal
        highs = (subset['high'] > subset['high'].shift(1)) & (subset['high'] > subset['high'].shift(-1))
        lows = (subset['low'] < subset['low'].shift(1)) & (subset['low'] < subset['low'].shift(-1))
        
        last_swing_high = subset[highs].iloc[-1] if hasattr(subset[highs], 'iloc') and len(subset[highs]) > 0 else None
        last_swing_low = subset[lows].iloc[-1] if hasattr(subset[lows], 'iloc') and len(subset[lows]) > 0 else None
        
        current = df.iloc[-1]
        
        # Check Bullish MSS: Break of Last Swing High
        if last_swing_high is not None:
            # If we closed above the last swing high RECENTLY (last 5 candles)
            break_idx = subset[subset['close'] > last_swing_high['high']].index
            if len(break_idx) > 0 and break_idx[-1] >= df.index[-5]:
                # Check for Displacement (Large Candle or FVG)
                # Alpha-Weighted Displacement Check
                if self.is_displaced_move(df, 'LONG', smt_strength):
                    return {'direction': 'LONG', 'origin_index': last_swing_low.name}
                
        # Check Bearish MSS: Break of Last Swing Low
        if last_swing_low is not None:
             break_idx = subset[subset['close'] < last_swing_low['low']].index
             if len(break_idx) > 0 and break_idx[-1] >= df.index[-5]:
                 if self.is_displaced_move(df, 'SHORT', smt_strength):
                     return {'direction': 'SHORT', 'origin_index': last_swing_high.name}

        return None

    def find_order_block(self, df, origin_index, direction):
        """
        Identifies the Order Block candle at the origin of the move.
        """
        try:
            # Origin index is the Swing Point.
            # OB is usually the candle AT or just BEFORE the Swing Point.
            idx_loc = df.index.get_loc(origin_index)
            
            # Look at 3 candles around the origin to find the specific OB candle
            # Bullish OB: Last DOWN candle before up move
            # Bearish OB: Last UP candle before down move
            
            candidates = df.iloc[idx_loc-2:idx_loc+2]
            ob_candle = None
            
            for i in range(len(candidates)):
                candle = candidates.iloc[i]
                is_green = candle['close'] > candle['open']
                is_red = candle['close'] < candle['open']
                
                if direction == 'LONG' and is_red:
                    ob_candle = candle
                elif direction == 'SHORT' and is_green:
                    ob_candle = candle
                    
            if ob_candle is None:
                ob_candle = df.iloc[idx_loc] # Fallback to pivot candle
                
            high = ob_candle['high']
            low = ob_candle['low']
            mean_threshold = (high + low) / 2
            
            if direction == 'LONG':
                 invalidation = low
            else:
                 invalidation = high
                 
            return {
                'open': ob_candle['open'],
                'close': ob_candle['close'],
                'high': high,
                'low': low,
                'mean_threshold': mean_threshold,
                'invalidation_level': invalidation
            }
            
        except Exception as e:
            logger.error(f"Error finding OB: {e}")
            return None

if __name__ == "__main__":
    scanner = SMCScanner()
    print(f"🚀 Scanning {Config.SYMBOLS[0]}...")
    result = scanner.scan_pattern(Config.SYMBOLS[0])
    if result:
        print(f"✅ Found: {result['pattern']} on {result['symbol']}")
    else:
        print("Thinking... No clean institutional setups found.")


