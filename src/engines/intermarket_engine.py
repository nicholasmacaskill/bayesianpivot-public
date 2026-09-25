import yfinance as yf
import pandas as pd
import logging
import time
import threading
from typing import Optional, Dict
from src.core.config import Config

try:
    yf.set_tz_cache_location("/tmp/yfinance_cache")
except Exception:
    pass

logger = logging.getLogger(__name__)

class IntermarketEngine:
    """
    Trinity of Sponsorship: Validates setups against DXY, 10Y Yields, and NQ/ES futures.
    ICT Theory: Institutional moves require cross-asset confirmation.
    SMT Divergence: If indices sweep but BTC doesn't, or vice-versa, reveals institutional intent.
    Bond Market: Rising yields = risk-off (bearish BTC), falling yields = risk-on (bullish BTC).
    """
    _cached_context: Dict = {}
    _last_fetch_time: float = 0.0
    _cache_ttl: float = 60.0  # 60s TTL cache for zero-latency scans
    _lock = threading.Lock()
    _is_fetching = False

    def __init__(self):
        self.symbols = {
            "NQ": "^IXIC",      # NASDAQ Composite
            "ES": "^GSPC",      # S&P 500
            "DXY": "DX-Y.NYB",   # US Dollar Index (Institutional SMT Key) - Stable Symbol
            "TNX": "^TNX"       # 10-Year Treasury Yield (Bond Market Sponsorship)
        }

    def _fetch_from_yfinance(self) -> Optional[Dict]:
        context = {}
        try:
            for key, ticker in self.symbols.items():
                data = yf.download(ticker, period="5d", interval=Config.TIMEFRAME, progress=False)
                if data is not None and len(data) > 2:
                    if isinstance(data.columns, pd.MultiIndex):
                        data.columns = data.columns.get_level_values(0)
                        
                    window = 12
                    subset = data.tail(window)
                    current_close = float(subset['Close'].iloc[-1])
                    prev_close = float(subset['Close'].iloc[0])
                    
                    change = (current_close - prev_close) / prev_close * 100
                    trend = "UP" if change > 0.005 else "DOWN" if change < -0.005 else "NEUTRAL"
                    
                    context[key] = {
                        "price": current_close,
                        "change_ltf": round(float(data['Close'].iloc[-1] - data['Close'].iloc[-2]) / data['Close'].iloc[-2] * 100, 3),
                        "trend": trend,
                        "high_1h": float(data['High'].iloc[-12:].max()),
                        "low_1h": float(data['Low'].iloc[-12:].min())
                    }
            return context
        except Exception as e:
            logger.error(f"Error fetching intermarket data: {e}")
            return None

    def get_market_context(self, force_refresh: bool = False) -> Optional[Dict]:
        now = time.time()
        
        # 1. Fast path: return cache if valid
        with IntermarketEngine._lock:
            if not force_refresh and IntermarketEngine._cached_context and (now - IntermarketEngine._last_fetch_time < IntermarketEngine._cache_ttl):
                return IntermarketEngine._cached_context
        
        # 2. Asynchronous background refresh if cache exists but is stale
        if IntermarketEngine._cached_context and not force_refresh:
            if not IntermarketEngine._is_fetching:
                IntermarketEngine._is_fetching = True
                def bg_worker():
                    try:
                        ctx = self._fetch_from_yfinance()
                        if ctx:
                            with IntermarketEngine._lock:
                                IntermarketEngine._cached_context = ctx
                                IntermarketEngine._last_fetch_time = time.time()
                    finally:
                        IntermarketEngine._is_fetching = False
                
                t = threading.Thread(target=bg_worker, daemon=True)
                t.start()
            return IntermarketEngine._cached_context
        
        # 3. Synchronous fetch on initial cold start
        ctx = self._fetch_from_yfinance()
        if ctx:
            with IntermarketEngine._lock:
                IntermarketEngine._cached_context = ctx
                IntermarketEngine._last_fetch_time = time.time()
        return IntermarketEngine._cached_context
    
    def calculate_cross_asset_divergence(self, btc_direction, context):
        """
        Sovereign Mode: Session-relative weighting for intermarket confluence.
        US Hours: Prioritize NQ/ES. 
        Asian/London: Prioritize DXY/TNX.
        """
        if not context:
            return 0
        
        from datetime import datetime, timezone
        utc_hour = datetime.now(timezone.utc).hour
        
        # Define weights based on session
        # US Equity Core (13:30 - 20:00 UTC)
        is_us_equities = 13 <= utc_hour < 20
        
        if is_us_equities:
             w_nq, w_dxy, w_tnx = 0.5, 0.3, 0.2
        else:
             w_nq, w_dxy, w_tnx = 0.1, 0.5, 0.4 # Crypto-native SMT (DXY/Yields)
        
        score = 0
        if 'TNX' in context:
            yield_trend = context['TNX']['trend']
            score += w_tnx if (btc_direction == 'LONG' and yield_trend == 'DOWN') or (btc_direction == 'SHORT' and yield_trend == 'UP') else 0.0
        
        if 'NQ' in context:
            nq_trend = context['NQ']['trend']
            score += w_nq if (btc_direction == 'LONG' and nq_trend == 'UP') or (btc_direction == 'SHORT' and nq_trend == 'DOWN') else 0.0
            
        if 'DXY' in context:
            dxy_trend = context['DXY']['trend']
            score += w_dxy if (btc_direction == 'LONG' and dxy_trend == 'DOWN') or (btc_direction == 'SHORT' and dxy_trend == 'UP') else 0.0
            
        return max(-1.0, min(1.0, score))

    def detect_true_smt(self, btc_df, correlated_symbol_key="DXY"):
        """
        ⭐ PHASE 2: True SMT Divergence Detection.
        Institutional Logic: A 'crack' in correlation reveals the real move.
        
        Returns: (smt_type_str, strength_score) or (None, 0.0)
        """
        correlated_ticker = self.symbols.get(correlated_symbol_key)
        if not correlated_ticker:
            return None, 0.0

        try:
            # 1. Fetch historical data for correlation (last 50 candles)
            corr_df = yf.download(correlated_ticker, period="5d", interval=Config.TIMEFRAME, progress=False)
            if corr_df is None or len(corr_df) < 20:
                return None, 0.0
            
            if isinstance(corr_df.columns, pd.MultiIndex):
                corr_df.columns = corr_df.columns.get_level_values(0)

            # 2. Get recent swing structure for both
            btc_recent = btc_df.tail(20)
            corr_recent = corr_df.tail(20)

            btc_h1, btc_h2 = btc_recent['high'].iloc[-10:-5].max(), btc_recent['high'].iloc[-5:].max()
            btc_l1, btc_l2 = btc_recent['low'].iloc[-10:-5].min(), btc_recent['low'].iloc[-5:].min()

            corr_h1, corr_h2 = corr_recent['High'].iloc[-10:-5].max(), corr_recent['High'].iloc[-5:].max()
            corr_l1, corr_l2 = corr_recent['Low'].iloc[-10:-5].min(), corr_recent['Low'].iloc[-5:].min()

            smt_detected = False
            smt_type = None
            strength = 0.0

            # 🟢 BULLISH SMT: Correlated asset (DXY/NQ) sweeps, but BTC holds (or vice-versa)
            if correlated_symbol_key == "DXY":
                # Inverse: DXY Higher High vs BTC Higher Low (BTC Refusal to follow DXY up)
                if corr_h2 > corr_h1 and btc_l2 > btc_l1:
                    smt_detected = True
                    smt_type = "BULLISH_SMT (DXY Sweep vs BTC Hold)"
                    # Calculate strength based on divergence magnitude (0.5 to 0.95)
                    dxy_sweep_pct = (corr_h2 - corr_h1) / corr_h1
                    btc_hold_pct = (btc_l2 - btc_l1) / btc_l1
                    strength = min(0.95, 0.5 + (dxy_sweep_pct + btc_hold_pct) * 100)
            else:
                # Direct (NQ/ES): Correlated index makes Lower Low while BTC makes Higher Low
                if corr_l2 < corr_l1 and btc_l2 > btc_l1:
                    smt_detected = True
                    smt_type = f"BULLISH_SMT ({correlated_symbol_key} Sweep vs BTC Hold)"
                    idx_sweep_pct = (corr_l1 - corr_l2) / corr_l1
                    btc_hold_pct = (btc_l2 - btc_l1) / btc_l1
                    strength = min(0.95, 0.5 + (idx_sweep_pct + btc_hold_pct) * 100)

            # 🔴 BEARISH SMT
            if not smt_detected:
                if correlated_symbol_key == "DXY":
                    # DXY Lower Low vs BTC Lower High
                    if corr_l2 < corr_l1 and btc_h2 < btc_h1:
                        smt_detected = True
                        smt_type = "BEARISH_SMT (DXY Sweep vs BTC Hold)"
                        dxy_sweep_pct = (corr_l1 - corr_l2) / corr_l1
                        btc_hold_pct = (btc_h1 - btc_h2) / btc_h1
                        strength = min(0.95, 0.5 + (dxy_sweep_pct + btc_hold_pct) * 100)
                else:
                    # Correlated index makes Higher High while BTC makes Lower High
                    if corr_h2 > corr_h1 and btc_h2 < btc_h1:
                        smt_detected = True
                        smt_type = f"BEARISH_SMT ({correlated_symbol_key} Sweep vs BTC Hold)"
                        idx_sweep_pct = (corr_h2 - corr_h1) / corr_h1
                        btc_hold_pct = (btc_h1 - btc_h2) / btc_h1
                        strength = min(0.95, 0.5 + (idx_sweep_pct + btc_hold_pct) * 100)

            if smt_detected:
                logger.info(f"⚡ TRUE SMT DETECTED: {smt_type} | Strength: {strength:.2f}")
                return smt_type, round(strength, 2)
            
            return None, 0.0

        except Exception as e:
            logger.error(f"Error in detect_true_smt: {e}")
            return None, 0.0

    def get_smt_strength(self, symbol, btc_df):
        """
        Multi-Asset SMT Divergence Evaluator.
        Evaluates cross-asset sponsorship across DXY, S&P 500 (ES), and Nasdaq (NQ).
        Returns highest confirmed divergence strength score.
        """
        best_strength = 0.0
        best_smt = None
        for key in ["DXY", "ES", "NQ"]:
            try:
                smt_type, strength = self.detect_true_smt(btc_df, correlated_symbol_key=key)
                if strength > best_strength:
                    best_strength = strength
                    best_smt = smt_type
            except Exception:
                pass
        if best_smt:
            logger.info(f"🏛️ [INTERMARKET SMT] Active Sponsorship: {best_smt} ({best_strength:.2f})")
        return best_strength

if __name__ == "__main__":
    engine = IntermarketEngine()
    print("Market Context:", engine.get_market_context())
