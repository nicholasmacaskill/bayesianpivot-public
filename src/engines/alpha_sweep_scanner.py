import numpy as np
import pandas as pd
import logging
from datetime import datetime, timezone
from src.core.config import Config
from src.engines.smc_scanner import SMCScanner
from src.core.database import log_scan, log_system_event
from src.clients.telegram_notifier import send_alert

from src.engines.shadow_substitution_engine import ShadowSubstitutionEngine
from src.engines.counterfactual_tracker import CounterfactualTracker
from src.engines.liquidity_heatmap_engine import LiquidityHeatmapEngine
from src.engines.retail_trap_engine import RetailStopTrapEngine
from src.engines.visual_vector_engine import VisualVectorEngine
from src.clients.tl_client import TradeLockerClient

logger = logging.getLogger(__name__)

class AlphaSweepScanner(SMCScanner):
    def __init__(self):
        super().__init__()
        self.shadow_engine = ShadowSubstitutionEngine()
        self.counterfactual_tracker = CounterfactualTracker()
        self.heatmap_engine = LiquidityHeatmapEngine()
        self.retail_trap_engine = RetailStopTrapEngine()
        self.visual_vector_engine = VisualVectorEngine()
        self.tl = TradeLockerClient()
        self._active_trade_brackets = {}
        self._position_tiers = {}
        try:
            from src.engines.execution_shadow_engine import ExecutionShadowEngine
            self.exec_shadow_engine = ExecutionShadowEngine()
            self.exec_shadow_engine.init_db()
        except Exception as se_err:
            logger.debug(f"ExecutionShadowEngine init error: {se_err}")
        try:
            from src.engines.live_orderflow_feed import LiveOrderflowFeed
            self.live_orderflow = LiveOrderflowFeed()
            self.live_orderflow.start()
        except Exception as of_err:
            logger.debug(f"LiveOrderflowFeed auto-start skipped: {of_err}")
        try:
            from src.engines.auction_market_engine import AuctionMarketEngine
            self.auction_engine = AuctionMarketEngine()
        except Exception as ame_err:
            logger.debug(f"AuctionMarketEngine init error: {ame_err}")
            self.auction_engine = None
        try:
            from src.engines.judas_inducement_engine import JudasInducementEngine
            self.judas_engine = JudasInducementEngine()
        except Exception as jie_err:
            logger.debug(f"JudasInducementEngine init error: {jie_err}")
            self.judas_engine = None
        logger.info("Bayesian Pivot Alpha Sweep Scanner Initialized with Live Orderflow Feed, Liquidity Heatmap, Judas Inducement Engine, Retail Trap Shadow Engine, Auction Market Engine & TradeLocker Fleet Client.")

    def is_premium_killzone(self, dt=None):
        """
        Returns the active killzone label, or None if outside active windows.
        Active Live Windows:
        - Asian Session & Judas Sweep: 00:00 - 06:00 UTC (17:00 - 23:00 PST)
        - London Open: 07:00 - 10:00 UTC (00:00 - 03:00 PST)
        - London Close / NY Morning: 13:30 - 16:00 UTC (06:30 - 09:00 PST)
        
        Shadow Observation Window:
        - NY Afternoon Shadow: 16:00 - 20:00 UTC (09:00 - 13:00 PST) [100% Shadow Tracking, Zero Live Risk]
        
        Dead Zone (Sleep / 0% Risk):
        - Post-NY Inter-Session Lull: 20:00 - 23:30 UTC (13:00 - 16:30 PST) -> None
        """
        if dt is None:
            dt = datetime.now(timezone.utc)
        utc_float = dt.hour + (dt.minute / 60.0)
        
        if 0.0 <= utc_float < 6.0:
            return "ASIAN_SESSION_JUDAS"
        elif 7.0 <= utc_float < 10.0:
            return "LONDON_OPEN"
        elif 12.0 <= utc_float <= 17.0:
            return "LONDON_CLOSE_NY_MORNING"
        elif 17.0 < utc_float < 20.0:
            return "NY_AFTERNOON_SHADOW"
        return None

    def get_relative_strength_leader(self) -> str:
        """
        Calculates the dynamic relative strength leader between BTC and ETH.
        Returns:
            'BTC_LEADER' if BTC is outperforming (BTC Dominance: BTC Longs & ETH Shorts aligned)
            'ETH_LEADER' if ETH is outperforming (Altseason: ETH Longs & BTC Shorts aligned)
        """
        try:
            df_btc = self.fetch_data('BTC/USD', '1h', limit=30, synchronized=False)
            df_eth = self.fetch_data('ETH/USD', '1h', limit=30, synchronized=False)
            if df_btc is not None and df_eth is not None and len(df_btc) >= 20 and len(df_eth) >= 20:
                min_len = min(len(df_btc), len(df_eth))
                ratio = df_eth['close'].iloc[-min_len:].values / df_btc['close'].iloc[-min_len:].values
                sma20 = pd.Series(ratio).rolling(20).mean().iloc[-1]
                current_ratio = ratio[-1]
                if current_ratio >= sma20:
                    return 'ETH_LEADER'
                else:
                    return 'BTC_LEADER'
        except Exception as e:
            logger.debug(f"Relative strength calculation fallback: {e}")
        return 'BTC_LEADER'

    def find_htf_levels(self, df_1h, window=2):
        """
        Finds recent high-timeframe swing highs and swing lows (fractals).
        A fractal has `window` lower candles on both sides.
        """
        highs = []
        lows = []
        
        # Iterate to find peaks
        for i in range(window, len(df_1h) - window):
            curr_high = df_1h['high'].iloc[i]
            curr_low = df_1h['low'].iloc[i]
            
            # Check swing high
            is_high = True
            for w in range(1, window + 1):
                if df_1h['high'].iloc[i - w] >= curr_high or df_1h['high'].iloc[i + w] >= curr_high:
                    is_high = False
                    break
            if is_high:
                highs.append((df_1h['timestamp'].iloc[i], curr_high))
                
            # Check swing low
            is_low = True
            for w in range(1, window + 1):
                if df_1h['low'].iloc[i - w] <= curr_low or df_1h['low'].iloc[i + w] <= curr_low:
                    is_low = False
                    break
            if is_low:
                lows.append((df_1h['timestamp'].iloc[i], curr_low))
                
        return highs, lows

    def check_turtle_soup(self, symbol, df_5m, df_1h):
        """
        Scans for the Turtle Soup setup (liquidity sweeps with wick rejection).
        Identical rules for Longs and Shorts to maintain mathematical symmetry.
        """
        if len(df_5m) < 15 or len(df_1h) < 50:
            return None

        # Calculate 5m ATR
        atr_series = self.calculate_atr(df_5m)
        if len(atr_series) == 0 or pd.isna(atr_series.iloc[-1]):
            return None
        atr_5m = atr_series.iloc[-1]
        
        # Latest completed 5m candle
        last_candle = df_5m.iloc[-2]
        c_open = last_candle['open']
        c_high = last_candle['high']
        c_low = last_candle['low']
        c_close = last_candle['close']
        c_range = max(c_high - c_low, 1e-8)
        
        # Find 1H levels (exclude very recent hours to avoid self-sweeps)
        # Use 1H data up to the last closed 1H bar
        df_1h_clean = df_1h.iloc[:-1]
        swing_highs, swing_lows = self.find_htf_levels(df_1h_clean, window=2)
        
        if not swing_highs and not swing_lows:
            return None
            
        # Get most recent levels
        recent_highs = [h[1] for h in swing_highs[-3:]] if swing_highs else []
        recent_lows = [l[1] for l in swing_lows[-3:]] if swing_lows else []
        
        # Hurst Exponent and Trend check
        closes_1h = df_1h['close'].values
        hurst = self.get_hurst_exponent(closes_1h)
        
        # 1H Trend (50 EMA)
        ema50 = df_1h['close'].ewm(span=50).mean().iloc[-1]
        trend = "UP" if closes_1h[-1] > ema50 else "DOWN"
        
        # Gate regime using Hurst:
        # Mean Reversion / Range Bound Chop: H <= 0.48 (Fades range extremes)
        # Trending: H > 0.55 (Trend continuation sweeps only)
        # Transitional / Low Edge: 0.48 < H <= 0.55 (Filtered out)
        if 0.48 < hurst <= 0.55:
            logger.info(f"Regime is Transitional (Hurst: {hurst:.3f}). Setup filtered out to maintain win rate.")
            return None
            
        is_trending = hurst > 0.55
        target_rr = getattr(Config, 'TARGET_RR', 3.0)
        min_stop_pct = getattr(Config, 'MIN_STOP_PCT', {}).get(symbol, 0.003)
        min_stop_dist = c_close * min_stop_pct
        buffer = max(atr_5m * 0.5, min_stop_dist * 0.5)
        
        # Long Setup (Sweep of Support)
        for level in recent_lows:
            # 5m candle low must pierce level, close must remain above level
            if c_low < level and c_close > level:
                sweep_dist = level - c_low
                # Validation check: ATR relative depth
                if 0.1 * atr_5m <= sweep_dist <= 1.5 * atr_5m:
                    # Wick rejection check (lower wick must be >= 30% of total candle range)
                    lower_wick = min(c_open, c_close) - c_low
                    if lower_wick / c_range >= 0.30:
                        # Trend alignment if trending
                        if is_trending and trend != "UP":
                            logger.info(f"Long setup blocked due to trend mismatch (Hurst: {hurst:.3f}, Trend: {trend})")
                            continue
                        
                        sl = round(c_low - buffer, 2)
                        if (c_close - sl) < min_stop_dist:
                            sl = round(c_close - min_stop_dist, 2)
                        risk_d = c_close - sl
                        if risk_d <= 0:
                            continue
                        tp = round(c_close + (risk_d * target_rr), 2)
                        
                        return {
                            "direction": "LONG",
                            "level": level,
                            "hurst": hurst,
                            "trend": trend,
                            "regime": "TRENDING" if is_trending else "MEAN_REVERSION",
                            "pattern_type": "TURTLE_SOUP_LIQUIDITY_SWEEP",
                            "sweep_dist": sweep_dist,
                            "atr": atr_5m,
                            "price": c_close,
                            "stop_loss": sl,
                            "take_profit": tp
                        }
                        
        # Short Setup (Sweep of Resistance)
        for level in recent_highs:
            # 5m candle high must pierce level, close must remain below level
            if c_high > level and c_close < level:
                sweep_dist = c_high - level
                # Validation check: ATR relative depth
                if 0.1 * atr_5m <= sweep_dist <= 1.5 * atr_5m:
                    # Wick rejection check (upper wick must be >= 30% of total candle range)
                    upper_wick = c_high - max(c_open, c_close)
                    if upper_wick / c_range >= 0.30:
                        # Trend alignment if trending
                        if is_trending and trend != "DOWN":
                            logger.info(f"Short setup blocked due to trend mismatch (Hurst: {hurst:.3f}, Trend: {trend})")
                            continue
                        
                        sl = round(c_high + buffer, 2)
                        if (sl - c_close) < min_stop_dist:
                            sl = round(c_close + min_stop_dist, 2)
                        risk_d = sl - c_close
                        if risk_d <= 0:
                            continue
                        tp = round(c_close - (risk_d * target_rr), 2)
                        
                        return {
                            "direction": "SHORT",
                            "level": level,
                            "hurst": hurst,
                            "trend": trend,
                            "regime": "TRENDING" if is_trending else "MEAN_REVERSION",
                            "pattern_type": "TURTLE_SOUP_LIQUIDITY_SWEEP",
                            "sweep_dist": sweep_dist,
                            "atr": atr_5m,
                            "price": c_close,
                            "stop_loss": sl,
                            "take_profit": tp
                        }
                        
        return None

    def check_breaker_block_mitigation(self, symbol, df_5m, df_1h, killzone=None):
        """
        Scans for Breaker Block Mitigations (Trend Continuation Pullback).
        STRICTLY RESTRICTED to High-Volume London Open and NY Open killzones.
        DISABLED in Asian session and off-hours to prevent chop traps.
        """
        if killzone not in ["LONDON_OPEN", "NY_OPEN"]:
            return None

        if len(df_5m) < 15 or len(df_1h) < 50:
            return None

        atr_series = self.calculate_atr(df_5m)
        if len(atr_series) == 0 or pd.isna(atr_series.iloc[-1]):
            return None
        atr_5m = atr_series.iloc[-1]
        
        last_candle = df_5m.iloc[-2]
        c_open = last_candle['open']
        c_high = last_candle['high']
        c_low = last_candle['low']
        c_close = last_candle['close']
        c_range = max(c_high - c_low, 1e-8)
        
        # Hurst Exponent and Trend check (Must be strongly trending)
        closes_1h = df_1h['close'].values
        hurst = self.get_hurst_exponent(closes_1h)
        if hurst <= 0.58:  # Enforce high-conviction persistent trend regime
            return None
            
        ema50 = df_1h['close'].ewm(span=50).mean().iloc[-1]
        trend = "UP" if closes_1h[-1] > ema50 else "DOWN"
        
        df_1h_clean = df_1h.iloc[:-1]
        swing_highs, swing_lows = self.find_htf_levels(df_1h_clean, window=2)
        
        # Long Breaker Block: Price broke above swing high, now testing it as support from above
        if trend == "UP" and swing_highs:
            recent_high = swing_highs[-1][1]
            if abs(c_low - recent_high) <= 0.5 * atr_5m and c_close > recent_high:
                lower_wick = min(c_open, c_close) - c_low
                if lower_wick / c_range >= 0.25:  # Lower wick proves support defense
                    return {
                        "direction": "LONG",
                        "level": recent_high,
                        "hurst": hurst,
                        "trend": trend,
                        "regime": "TRENDING_EXPANSION",
                        "pattern_type": "BREAKER_BLOCK_MITIGATION",
                        "sweep_dist": abs(c_low - recent_high),
                        "atr": atr_5m,
                        "price": c_close
                    }
                    
        # Short Breaker Block: Price broke below swing low, now testing it as resistance from below
        if trend == "DOWN" and swing_lows:
            recent_low = swing_lows[-1][1]
            if abs(c_high - recent_low) <= 0.5 * atr_5m and c_close < recent_low:
                upper_wick = c_high - max(c_open, c_close)
                if upper_wick / c_range >= 0.25:  # Upper wick proves resistance defense
                    return {
                        "direction": "SHORT",
                        "level": recent_low,
                        "hurst": hurst,
                        "trend": trend,
                        "regime": "TRENDING_EXPANSION",
                        "pattern_type": "BREAKER_BLOCK_MITIGATION",
                        "sweep_dist": abs(c_high - recent_low),
                        "atr": atr_5m,
                        "price": c_close
                    }
                    
        return None

    def check_london_close_silver_bullet(self, symbol, df_5m, df_1h, killzone):
        """
        Scans for London Close Silver Bullet (14:00 - 16:00 UTC / 10:00 - 11:00 AM EST).
        Only active in Mean-Reverting Regimes (Hurst < 0.45).
        Enters when price is extended >= 2.0 sigma away from VWAP and sweeps morning extremes.
        """
        now_hour = datetime.now(timezone.utc).hour
        if not (14 <= now_hour <= 16 or killzone == "NY_OPEN"):
            return None
            
        if len(df_5m) < 20 or len(df_1h) < 50:
            return None

        atr_series = self.calculate_atr(df_5m)
        if len(atr_series) == 0 or pd.isna(atr_series.iloc[-1]):
            return None
        atr_5m = atr_series.iloc[-1]
        
        last_candle = df_5m.iloc[-2]
        c_open = last_candle['open']
        c_high = last_candle['high']
        c_low = last_candle['low']
        c_close = last_candle['close']
        c_range = max(c_high - c_low, 1e-8)
        
        closes_1h = df_1h['close'].values
        hurst = self.get_hurst_exponent(closes_1h)
        if hurst >= 0.48:  # Must be mean-reverting or exhausted
            return None
            
        # Calculate Rolling Session VWAP & Dispersion Bands
        typical_price = (df_5m['high'] + df_5m['low'] + df_5m['close']) / 3.0
        vol = df_5m['volume'].replace(0, 1.0)
        cum_vol = vol.rolling(24).sum()
        cum_pv = (typical_price * vol).rolling(24).sum()
        vwap = (cum_pv / cum_vol).iloc[-2]
        rolling_std = df_5m['close'].rolling(24).std().iloc[-2]
        
        if pd.isna(vwap) or pd.isna(rolling_std) or rolling_std <= 0:
            return None
            
        upper_band = vwap + (2.0 * rolling_std)
        lower_band = vwap - (2.0 * rolling_std)
        
        # Bearish Silver Bullet: Price pierced above +2.0 sigma band with upper wick rejection
        if c_high >= upper_band and c_close < upper_band:
            upper_wick = c_high - max(c_open, c_close)
            if upper_wick / c_range >= 0.30:
                return {
                    "direction": "SHORT",
                    "level": upper_band,
                    "hurst": hurst,
                    "trend": "DOWN_REVERSAL",
                    "regime": "LONDON_CLOSE_REBALANCE",
                    "pattern_type": "LONDON_CLOSE_SILVER_BULLET",
                    "sweep_dist": c_high - upper_band,
                    "atr": atr_5m,
                    "price": c_close
                }
                
        # Bullish Silver Bullet: Price pierced below -2.0 sigma band with lower wick rejection
        if c_low <= lower_band and c_close > lower_band:
            lower_wick = min(c_open, c_close) - c_low
            if lower_wick / c_range >= 0.30:
                return {
                    "direction": "LONG",
                    "level": lower_band,
                    "hurst": hurst,
                    "trend": "UP_REVERSAL",
                    "regime": "LONDON_CLOSE_REBALANCE",
                    "pattern_type": "LONDON_CLOSE_SILVER_BULLET",
                    "sweep_dist": lower_band - c_low,
                    "atr": atr_5m,
                    "price": c_close
                }
                
        return None

    def check_ny_hft_double_sweep_shadow(self, symbol, df_5m, df_1h, killzone):
        """
        Scans for NY HFT Predatory Double Sweeps (06:30 - 09:30 AM PST / 13:30 - 16:30 UTC).
        100% SHADOW ONLY - ZERO LIVE CAPITAL RISK.
        Detects when both highs and lows are swept within 60 mins to purge retail liquidity.
        """
        now_hour = datetime.now(timezone.utc).hour
        now_min = datetime.now(timezone.utc).minute
        utc_time_float = now_hour + (now_min / 60.0)
        
        # NY Morning Liquidity Purge Window (13:30 - 16:30 UTC / 09:30 AM - 12:30 PM EST)
        if not (13.5 <= utc_time_float <= 16.5 or killzone == "NY_OPEN"):
            return None

        if len(df_5m) < 25 or len(df_1h) < 50:
            return None

        atr_series = self.calculate_atr(df_5m)
        if len(atr_series) == 0 or pd.isna(atr_series.iloc[-1]):
            return None
        atr_5m = atr_series.iloc[-1]

        recent_bars = df_5m.iloc[-14:-2]
        last_candle = df_5m.iloc[-2]
        c_open = last_candle['open']
        c_high = last_candle['high']
        c_low = last_candle['low']
        c_close = last_candle['close']
        c_range = max(c_high - c_low, 1e-8)

        df_1h_clean = df_1h.iloc[:-1]
        swing_highs, swing_lows = self.find_htf_levels(df_1h_clean, window=2)
        if not swing_highs or not swing_lows:
            return None

        recent_high = swing_highs[-1][1]
        recent_low = swing_lows[-1][1]

        high_was_pierced = any(recent_bars['high'] > recent_high)
        low_was_pierced = any(recent_bars['low'] < recent_low)

        closes_1h = df_1h['close'].values
        hurst = self.get_hurst_exponent(closes_1h)

        # Bullish Post-Purge Reversal: High was swept earlier (trapping longs), low swept now (trapping shorts)
        if high_was_pierced and c_low < recent_low and c_close > recent_low:
            lower_wick = min(c_open, c_close) - c_low
            if lower_wick / c_range >= 0.30:
                return {
                    "direction": "LONG",
                    "level": recent_low,
                    "hurst": hurst,
                    "trend": "DOUBLE_SWEEP_REVERSAL",
                    "regime": "NY_HFT_LIQUIDITY_PURGE",
                    "pattern_type": "NY_HFT_DOUBLE_SWEEP_SHADOW",
                    "sweep_dist": recent_low - c_low,
                    "atr": atr_5m,
                    "price": c_close,
                    "is_shadow_only": True
                }

        # Bearish Post-Purge Reversal: Low was swept earlier (trapping shorts), high swept now (trapping longs)
        if low_was_pierced and c_high > recent_high and c_close < recent_high:
            upper_wick = c_high - max(c_open, c_close)
            if upper_wick / c_range >= 0.30:
                return {
                    "direction": "SHORT",
                    "level": recent_high,
                    "hurst": hurst,
                    "trend": "DOUBLE_SWEEP_REVERSAL",
                    "regime": "NY_HFT_LIQUIDITY_PURGE",
                    "pattern_type": "NY_HFT_DOUBLE_SWEEP_SHADOW",
                    "sweep_dist": c_high - recent_high,
                    "atr": atr_5m,
                    "price": c_close,
                    "is_shadow_only": True
                }

        return None

    def check_retail_trap_shadow(self, symbol, df_5m, df_1h, killzone):
        """
        Scans for Retail LuxAlgo BOS/CHoCH Trap Fades.
        100% SHADOW LAB ONLY - ZERO LIVE CAPITAL RISK.
        """
        trap = self.retail_trap_engine.detect_retail_trap(df_5m, df_1h)
        if not trap:
            return None
            
        atr_series = self.calculate_atr(df_5m)
        atr_5m = atr_series.iloc[-1] if len(atr_series) > 0 and not pd.isna(atr_series.iloc[-1]) else 15.0
        
        closes_1h = df_1h['close'].values
        hurst = self.get_hurst_exponent(closes_1h)
        
        return {
            "direction": trap["direction"],
            "level": trap["breakout_level"],
            "hurst": hurst,
            "trend": "RETAIL_TRAP_FADE",
            "regime": "RETAIL_INDUCEMENT_PURGE",
            "pattern_type": "RETAIL_LUXALGO_TRAP_SHADOW",
            "sweep_dist": abs(trap["price"] - trap["breakout_level"]),
            "atr": atr_5m,
            "price": trap["price"],
            "is_shadow_only": True,
            "retail_reasoning": trap["reasoning"],
            "target_stop_pool": trap["target_stop_pool"]
        }

    def check_strong_smt_sweep_shadow(self, symbol, df_5m, df_1h, killzone):
        """
        Scans for Strong SMT Divergence Sweeps during Premium Killzones.
        100% SHADOW LAB ONLY - ZERO LIVE CAPITAL RISK.
        """
        try:
            index_context = self.intermarket.get_market_context()
            if not index_context:
                return None
                
            atr_series = self.calculate_atr(df_5m)
            atr_5m = atr_series.iloc[-1] if len(atr_series) > 0 and not pd.isna(atr_series.iloc[-1]) else 15.0
            
            c_high = df_5m.iloc[-2]['high']
            c_low = df_5m.iloc[-2]['low']
            c_open = df_5m.iloc[-2]['open']
            c_close = df_5m.iloc[-2]['close']
            c_range = max(c_high - c_low, 1e-8)
            
            recent_high = df_5m.iloc[-20:-2]['high'].max()
            recent_low = df_5m.iloc[-20:-2]['low'].min()
            
            # Bullish SMT: Low swept, bullish rejection wick >= 40%, SMT score >= 0.70
            if c_low < recent_low and c_close > recent_low:
                lower_wick = min(c_open, c_close) - c_low
                if lower_wick / c_range >= 0.40:
                    smt_score = self.intermarket.calculate_cross_asset_divergence('LONG', index_context)
                    if smt_score >= 0.70:
                        closes_1h = df_1h['close'].values
                        hurst = self.get_hurst_exponent(closes_1h)
                        return {
                            "direction": "LONG",
                            "level": recent_low,
                            "hurst": hurst,
                            "trend": "SMT_DIVERGENCE_SWEEP",
                            "regime": "INTERMARKET_SPONSORSHIP_EXPANSION",
                            "pattern_type": "SMT_DIVERGENCE_SWEEP_SHADOW",
                            "sweep_dist": recent_low - c_low,
                            "atr": atr_5m,
                            "price": c_close,
                            "is_shadow_only": True,
                            "smt_strength": smt_score
                        }
                        
            # Bearish SMT: High swept, bearish rejection wick >= 40%, SMT score >= 0.70
            if c_high > recent_high and c_close < recent_high:
                upper_wick = c_high - max(c_open, c_close)
                if upper_wick / c_range >= 0.40:
                    smt_score = self.intermarket.calculate_cross_asset_divergence('SHORT', index_context)
                    if smt_score >= 0.70:
                        closes_1h = df_1h['close'].values
                        hurst = self.get_hurst_exponent(closes_1h)
                        return {
                            "direction": "SHORT",
                            "level": recent_high,
                            "hurst": hurst,
                            "trend": "SMT_DIVERGENCE_SWEEP",
                            "regime": "INTERMARKET_SPONSORSHIP_EXPANSION",
                            "pattern_type": "SMT_DIVERGENCE_SWEEP_SHADOW",
                            "sweep_dist": c_high - recent_high,
                            "atr": atr_5m,
                            "price": c_close,
                            "is_shadow_only": True,
                            "smt_strength": smt_score
                        }
        except Exception as e:
            logger.debug(f"SMT shadow check error: {e}")
        return None

    def check_fvg_50pct_ce_midpoint_shadow(self, symbol, df_5m, df_1h, killzone):
        """
        Scans for 50% Consequent Encroachment (CE) FVG Midpoint Reversals.
        Strategy 5: Graduated to Live Execution for Gold Longs (XAU/USD).
        Shorts and non-killzone/counter-trend setups are strictly quarantined to Shadow Lab.
        """
        if len(df_1h) < 5 or len(df_5m) < 5:
            return None
            
        try:
            atr_series = self.calculate_atr(df_5m)
            atr_5m = atr_series.iloc[-1] if len(atr_series) > 0 and not pd.isna(atr_series.iloc[-1]) else 15.0
            
            c_close_5m = df_5m.iloc[-2]['close']
            c_low_5m = df_5m.iloc[-2]['low']
            c_high_5m = df_5m.iloc[-2]['high']
            
            is_gold = symbol in ["XAU/USD", "XAUUSD", "GOLD"]
            now_utc = datetime.now(timezone.utc)
            is_weekday = now_utc.weekday() < 5
            kz_str = str(killzone or "").upper()
            is_gold_liquid_session = any(k in kz_str for k in ["LONDON", "NY", "NEW_YORK", "CONTINUOUS", "ASIAN"])
            
            # 1H HTF Trend Check (Mandatory Bullish Structure for Longs)
            ema50_1h = df_1h['close'].ewm(span=min(50, len(df_1h))).mean().iloc[-1]
            htf_bullish = df_1h['close'].iloc[-1] >= ema50_1h

            # Scan 1H for recent unmitigated Fair Value Gaps
            for i in range(len(df_1h) - 4, len(df_1h) - 1):
                c1_high = float(df_1h.iloc[i-1]['high'])
                c1_low = float(df_1h.iloc[i-1]['low'])
                c3_low = float(df_1h.iloc[i+1]['low'])
                c3_high = float(df_1h.iloc[i+1]['high'])
                
                # Bullish FVG: Gap between C1 High and C3 Low
                if c3_low > c1_high:
                    ce_level = (c1_high + c3_low) / 2.0
                    if c_low_5m <= ce_level and c_close_5m > ce_level:
                        closes_1h = df_1h['close'].values
                        hurst = self.get_hurst_exponent(closes_1h)
                        
                        # Stop loss placed below FVG bottom (c1_high) with ATR buffer
                        stop_buffer = atr_5m * getattr(Config, 'STRATEGY_5_STOP_BUFFER_ATR', 0.25)
                        sl_price = c1_high - stop_buffer
                        stop_dist = abs(c_close_5m - sl_price)
                        target_rr = getattr(Config, 'STRATEGY_5_TARGET_RR', 2.5)
                        tp_price = c_close_5m + (stop_dist * target_rr)

                        # Determine if qualified for Live Champion Execution
                        strat_5_auto = getattr(Config, 'STRATEGY_5_AUTO_EXECUTE', False)
                        is_live_qualified = (
                            is_gold 
                            and is_weekday 
                            and is_gold_liquid_session 
                            and htf_bullish 
                            and strat_5_auto
                        )

                        return {
                            "direction": "LONG",
                            "level": ce_level,
                            "hurst": hurst,
                            "trend": "50PCT_CE_FVG_REVERSAL",
                            "regime": "FVG_CONSEQUENT_ENCROACHMENT",
                            "pattern_type": "STRAT_5_XAU_GOLD_50PCT_CE_LONG" if is_live_qualified else "FVG_50PCT_CE_REVERSAL_SHADOW",
                            "strategy_id": "STRATEGY_5_50PCT_CE_MT",
                            "sweep_dist": abs(c_close_5m - ce_level),
                            "atr": atr_5m,
                            "price": c_close_5m,
                            "stop_loss": sl_price,
                            "take_profit": tp_price,
                            "is_shadow_only": not is_live_qualified,
                            "fvg_top": c3_low,
                            "fvg_bottom": c1_high
                        }
                        
                # Bearish FVG: Gap between C1 Low and C3 High
                if c3_high < c1_low:
                    # Directional Rule: Strategy 5 prohibits shorting Gold (12.8% WR toxic failure)
                    # Shorts remain 100% Shadow Only for research
                    ce_level = (c1_low + c3_high) / 2.0
                    if c_high_5m >= ce_level and c_close_5m < ce_level:
                        closes_1h = df_1h['close'].values
                        hurst = self.get_hurst_exponent(closes_1h)
                        sl_price = c1_low + (atr_5m * 0.25)
                        stop_dist = abs(sl_price - c_close_5m)
                        tp_price = c_close_5m - (stop_dist * 2.5)
                        return {
                            "direction": "SHORT",
                            "level": ce_level,
                            "hurst": hurst,
                            "trend": "50PCT_CE_FVG_REVERSAL",
                            "regime": "FVG_CONSEQUENT_ENCROACHMENT",
                            "pattern_type": "FVG_50PCT_CE_REVERSAL_SHADOW",
                            "strategy_id": "STRATEGY_5_50PCT_CE_MT",
                            "sweep_dist": abs(c_close_5m - ce_level),
                            "atr": atr_5m,
                            "price": c_close_5m,
                            "stop_loss": sl_price,
                            "take_profit": tp_price,
                            "is_shadow_only": True, # Always shadow for shorts
                            "fvg_top": c1_low,
                            "fvg_bottom": c3_high
                        }
        except Exception as e:
            logger.debug(f"50% CE FVG shadow check error: {e}")
            
        return None

    def scan_symbol(self, symbol, is_shadow=False):
        """
        Runs the Bayesian Pivot Alpha scan on the given symbol.
        """
        is_symbol_shadow = is_shadow or (symbol in getattr(Config, 'SHADOW_SYMBOLS', []))
        killzone = self.is_premium_killzone()
        if not killzone:
            logger.info(f"Skipping {symbol} scan: Outside premium Killzones.")
            return None
            
        logger.info(f"Scanning {symbol} inside {killzone}{' [SHADOW LAB]' if is_symbol_shadow else ''}...")
        
        # Fetch 1H and 5m data
        df_1h = self.fetch_data(symbol, '1h', limit=100, synchronized=False)
        df_5m = self.fetch_data(symbol, '5m', limit=100, synchronized=False)
        
        if df_1h is None or df_5m is None:
            logger.warning(f"Failed to fetch data for {symbol}.")
            return None
            
        # 1. Primary Champion Hunt: Judas Outlier Inducement Hunter (Strategy 9)
        setup = None
        if getattr(Config, 'STRATEGY_9_ENABLED', True) and getattr(self, 'judas_engine', None):
            try:
                j_setup = self.judas_engine.evaluate_dataframe(df_5m, symbol=symbol)
                if j_setup:
                    h_val = self.get_hurst_exponent(df_1h['close'].values) if df_1h is not None and len(df_1h) >= 20 else 0.60
                    setup = {
                        'pattern_type': 'JUDAS_INDUCEMENT_SNIPER',
                        'strategy_id': 'STRATEGY_9_JUDAS_INDUCEMENT',
                        'direction': j_setup['direction'],
                        'price': j_setup['entry_price'],
                        'level': j_setup['entry_price'],
                        'stop_loss': j_setup['stop_loss'],
                        'take_profit': j_setup['take_profit'],
                        'atr': j_setup['atr_20'],
                        'regime': 'VOLATILITY_EXPANSION_INDUCEMENT',
                        'hurst': h_val,
                        'is_shadow_only': False
                    }
                    logger.info(f"🎯 [STRATEGY 9 CHAMPION] Detected Judas Inducement setup on {symbol}: {j_setup['direction']} @ {j_setup['entry_price']}")
            except Exception as j_err:
                logger.error(f"Strategy 9 Judas hunt error: {j_err}")

        # 2. Secondary Hunt: Turtle Soup Liquidity Sweeps (Mean Reversion Chop H <= 0.48)
        if not setup:
            setup = self.check_turtle_soup(symbol, df_5m, df_1h)
        
        # 3. Tertiary Hunt: London Close Silver Bullet (10-11 AM EST Rebalance)
        if not setup:
            setup = self.check_london_close_silver_bullet(symbol, df_5m, df_1h, killzone)

        # 4. Quaternary Hunt: 50% Consequent Encroachment FVG Fill (Strategy 5 - Live Champion for Gold Longs)
        if not setup:
            setup = self.check_fvg_50pct_ce_midpoint_shadow(symbol, df_5m, df_1h, killzone)
            
        # 5. Quinary Hunt: NY HFT Double-Sweep Purge (100% Zero-Risk Shadow Tracking)
        if not setup:
            setup = self.check_ny_hft_double_sweep_shadow(symbol, df_5m, df_1h, killzone)
            
        # 6. Senary Hunt: Retail LuxAlgo Trap Fade (100% Zero-Risk Shadow Tracking)
        if not setup:
            setup = self.check_retail_trap_shadow(symbol, df_5m, df_1h, killzone)

        # 7. Septenary Hunt: Strong SMT Divergence Sweep (100% Zero-Risk Shadow Tracking)
        if not setup:
            setup = self.check_strong_smt_sweep_shadow(symbol, df_5m, df_1h, killzone)

        # 8. Octonary Hunt: Non-ICT Quantitative Microstructure Contenders (Graduated Live Probes & Shadow Tracking)
        if not setup and getattr(self, 'auction_engine', None):
            try:
                non_ict_setups = self.auction_engine.evaluate_all_shadow_contenders(
                    symbol=symbol,
                    df_5m=df_5m,
                    df_1h=df_1h,
                    live_orderflow=getattr(self, 'live_orderflow', None)
                )
                if non_ict_setups:
                    best_non_ict = non_ict_setups[0]
                    atr_calc = float((df_5m['high'] - df_5m['low']).rolling(14).mean().iloc[-1])

                    graduated_auction_patterns = [
                        "AVWAP_2SIGMA_BEARISH_SNAPBACK",
                        "AVWAP_2SIGMA_BULLISH_SNAPBACK",
                        "WYCKOFF_VSA_SPRING",
                        "WYCKOFF_VSA_UPTHRUST",
                        "AMT_VALUE_AREA_HIGH_REJECTION",
                        "AMT_VALUE_AREA_LOW_REJECTION"
                    ]
                    is_live_symbol = symbol in getattr(Config, 'SYMBOLS', ['BTC/USD', 'XAU/USD'])
                    is_graduated = (
                        best_non_ict['pattern'] in graduated_auction_patterns
                        and is_live_symbol
                        and getattr(Config, 'AUCTION_MARKET_AUTO_EXECUTE', True)
                    )

                    setup = {
                        'pattern_type': best_non_ict['pattern'],
                        'strategy_id': best_non_ict['strategy_id'],
                        'direction': best_non_ict['direction'],
                        'price': best_non_ict['entry'],
                        'level': best_non_ict.get('vah', best_non_ict.get('val', best_non_ict['entry'])),
                        'stop_loss': best_non_ict['stop_loss'],
                        'take_profit': best_non_ict['target'],
                        'atr': atr_calc,
                        'regime': 'AUCTION_DISCOVERY',
                        'hurst': 0.40,
                        'is_shadow_only': not is_graduated,
                        'is_probe_sizing': is_graduated
                    }
            except Exception as non_ict_err:
                logger.debug(f"Non-ICT hunt error: {non_ict_err}")
            
        if setup:
            pattern_type = setup.get('pattern_type', 'TURTLE_SOUP_LIQUIDITY_SWEEP')
            
            # ── DEDUPLICATION & COOLDOWN GATE (Candle Timestamp & 60 Mins) ──
            import time
            raw_ts = df_5m.iloc[-2]['timestamp'] if 'timestamp' in df_5m.columns else time.time()
            candle_ts = int(pd.Timestamp(raw_ts).timestamp()) if isinstance(raw_ts, (pd.Timestamp, datetime)) else int(float(raw_ts))
            cache_key = (symbol, setup['direction'], pattern_type, candle_ts)
            now_ts = time.time()
            if cache_key in self._signal_cache:
                logger.info(f"Skipping redundant signal for {symbol} {setup['direction']} ({pattern_type}) on candle {candle_ts}: Already processed.")
                return None
            self._signal_cache[cache_key] = now_ts

            logger.info(f"🏆 BAYESIAN PIVOT ALPHA SETUP DETECTED ({pattern_type}): {symbol} {setup['direction']} at {setup['price']}")
            
            # Format base pattern string
            base_pattern_str = f"Bayesian Pivot {pattern_type.replace('_', ' ')} {setup['direction']} ({setup['regime']})"
            
            # Dynamic Risk and Sizing Calculations
            entry_price = setup['price']
            atr_val = setup['atr']
            if setup.get('stop_loss') is not None and setup.get('take_profit') is not None:
                sl_price = float(setup['stop_loss'])
                tp_price = float(setup['take_profit'])
                stop_distance = abs(entry_price - sl_price)
            else:
                stop_distance = atr_val * getattr(Config, 'STOP_LOSS_ATR_MULTIPLIER', 2.5)
                target_rr = getattr(Config, 'TARGET_RR', 3.0)
                # Stop Loss
                if setup['direction'] == 'LONG':
                    sl_price = entry_price - stop_distance
                    tp_price = entry_price + (stop_distance * target_rr)
                else:
                    sl_price = entry_price + stop_distance
                    tp_price = entry_price - (stop_distance * target_rr)

            # ── UNIFIED MASTER VOLATILITY & MINIMUM STOP FLOOR ──
            # Enforces that EVERY trade type has sufficient breathing room outside market noise
            min_stop_pct = getattr(Config, 'MIN_STOP_PCT', {}).get(symbol, 0.003)
            min_stop_dist = entry_price * min_stop_pct
            min_atr_dist = atr_val * getattr(Config, 'MIN_STOP_LOSS_ATR', 1.8)
            effective_min_stop = max(min_stop_dist, min_atr_dist)

            if stop_distance < effective_min_stop:
                orig_sl = sl_price
                orig_dist = stop_distance
                stop_distance = effective_min_stop
                if setup['direction'] == 'LONG':
                    sl_price = round(entry_price - stop_distance, 2)
                else:
                    sl_price = round(entry_price + stop_distance, 2)
                # Recalculate target to maintain the intended R:R ratio
                target_rr = round(abs(tp_price - entry_price) / max(orig_dist, 1e-4), 2)
                if target_rr < 2.0:
                    target_rr = 2.5
                if setup['direction'] == 'LONG':
                    tp_price = round(entry_price + (stop_distance * target_rr), 2)
                else:
                    tp_price = round(entry_price - (stop_distance * target_rr), 2)
                logger.info(f"🛡️ [MASTER VOLATILITY GUARD] Widened {symbol} stop distance from ${orig_dist:.2f} to ${stop_distance:.2f} (Floor: {min_stop_pct*100:.2f}% / 1.8x ATR). Position size scaled down proportionally.")

            # Update setup dictionary with volatility-protected levels
            setup['stop_loss'] = sl_price
            setup['take_profit'] = tp_price
                
            # Base risk amount
            risk_amt = getattr(Config, 'FIXED_RISK_USD', 100.0)
            if setup['direction'] == 'LONG':
                risk_amt = risk_amt * getattr(Config, 'LONG_RISK_MULTIPLIER', 1.0)
                
            max_risk = getattr(Config, 'MAX_RISK_USD', 150.0)
            if risk_amt > max_risk:
                risk_amt = max_risk
                
            contract_size = Config.get_contract_size(symbol)

            # Calculate position size (lots) with exact broker contract size multiplier
            denom = stop_distance * contract_size
            lots = round(risk_amt / denom, 4) if denom > 0 else 0
            
            # Symbol Cap
            max_allowed_size = getattr(Config, 'MAX_POSITION_SIZES', {}).get(symbol)
            if max_allowed_size is not None and lots > max_allowed_size:
                lots = max_allowed_size
                
            # Notional Cap
            position_value = lots * entry_price * contract_size
            max_notional = getattr(Config, 'MAX_NOTIONAL_VALUE_USD', 50000.0)
            if position_value > max_notional:
                lots = round(max_notional / (entry_price * contract_size), 4)
                position_value = lots * entry_price * contract_size
                
            # Take Profit Clamping (Max Profit USD cap / 20% consistency rule)
            max_profit = getattr(Config, 'MAX_PROFIT_USD', 400.0)
            if lots > 0:
                potential_profit = lots * abs(tp_price - entry_price) * contract_size
                if potential_profit > max_profit:
                    tp_step = max_profit / (lots * contract_size)
                    if setup['direction'] == 'LONG':
                        tp_price = entry_price + tp_step
                    else:
                        tp_price = entry_price - tp_step
            
            # Run Shadow Substitution Audit (Quantitative AI Confluence Tracking)
            try:
                shadow_report = self.shadow_engine.run_shadow_audit(symbol, df_5m, setup['direction'])
                shadow_score = float(shadow_report.get('shadow_score', 5.0))
                logger.info(f"👻 Shadow Substitution Score for {symbol}: {shadow_score}/10 | CVD: {shadow_report.get('cvd_absorption', {}).get('details', 'N/A')} | VWAP Z-Score: {shadow_report.get('session_vwap', {}).get('z_score', 0):.2f}")
            except Exception as shadow_err:
                logger.warning(f"Shadow substitution audit error: {shadow_err}")
                shadow_report = {}
                shadow_score = 5.0

            # ── INSTITUTIONAL LIQUIDITY HEATMAP AUDIT (HIGHEST WEIGHT CRITERIA) ──
            try:
                is_dense_liq, liq_density, liq_msg, dynamic_tp = self.heatmap_engine.audit_sweep_against_heatmap(
                    sweep_price=setup['level'],
                    direction=setup['direction'],
                    df_5m=df_5m,
                    df_1h=df_1h
                )
                logger.info(f"🎯 [LIQUIDITY HEATMAP] {symbol} {setup['direction']}: {liq_msg}")

                # Dynamic TP Magnet Snapping (if opposing stop cluster offers >= 2.0R distance)
                if dynamic_tp is not None:
                    dyn_dist = abs(dynamic_tp - entry_price)
                    if dyn_dist >= stop_distance * 2.0:
                        tp_price = dynamic_tp
                        logger.info(f"🧲 [MAGNET TARGET] Snapped Take Profit to opposing stop cluster @ ${tp_price:.2f} ({dyn_dist/stop_distance:.2f}R)")

                # Weight Liquidity Density as Primary Confluence (Highest Weight)
                if is_dense_liq:
                    # +2.0 boost for dense stop clusters (PDH/PDL, EQL/EQH, Asian extremes)
                    shadow_score = min(shadow_score + (liq_density * 0.25), 10.0)
                else:
                    # Penalize sweeps of random / isolated noise levels
                    shadow_score = max(shadow_score - 1.5, 3.0)
            except Exception as liq_err:
                logger.warning(f"Liquidity heatmap audit error: {liq_err}")
                is_dense_liq = False  # Safe default: treat as low-density on error
                liq_density = 0.0
                liq_msg = "Heatmap audit unavailable"
                dynamic_tp = None

            # ── NON-ICT MODULAR CONFLUENCE BOOSTERS (AMT, AVWAP, ORDER FLOW ABSORPTION) ──
            boost_reasons = []
            if getattr(self, 'auction_engine', None):
                try:
                    boost, boost_reasons = self.auction_engine.get_confluence_boost(
                        symbol=symbol,
                        direction=setup['direction'],
                        entry_price=entry_price,
                        df_5m=df_5m,
                        df_1h=df_1h,
                        live_orderflow=getattr(self, 'live_orderflow', None)
                    )
                    if boost > 0:
                        shadow_score = min(shadow_score + boost, 10.0)
                        logger.info(f"🏛️ [AUCTION & MICROSTRUCTURE CONFLUENCE] +{boost:.1f} Score Boost: {', '.join(boost_reasons)} -> New Score: {shadow_score:.1f}/10")
                except Exception as conf_err:
                    logger.debug(f"Auction confluence boost error: {conf_err}")

            # ── HARD DENSITY GATE: Low-density sweeps are forced to shadow-only ──
            # A density < 6.0 means price swept a random local swing, not an
            # institutional stop cluster. No live capital is ever risked on noise.
            is_low_density_sweep = not is_dense_liq

            # ── VISUAL VECTOR GEOMETRIC SIMILARITY (SHADOW METADATA — NEVER GATES) ──
            # Computes a 64-dim geometric feature vector from the live 5m candle geometry
            # and performs cosine similarity search against all historical outcomes.
            # This NEVER blocks a trade — it only logs data for future validation.
            # Promote to a hard gate only after 4–6 weeks of shadow data confirms predictive value.
            vec_result = {
                'recommendation': 'NEUTRAL',
                'confidence': 0.0,
                'win_rate': 50.0,
                'avg_r': 0.0,
                'key_reason': 'Visual vector initializing'
            }
            try:
                active_regime = setup.get('regime', setup.get('regime_type', 'UNKNOWN'))
                query_vec = self.visual_vector_engine.extract_geometric_features(df_5m, setup=setup)
                vec_result = self.visual_vector_engine.evaluate_visual_precedent(
                    query_vec, symbol=symbol, direction=setup['direction'],
                    regime_type=active_regime
                )
                logger.info(
                    f"🔮 [VISUAL VECTOR] {symbol} {setup['direction']}: {vec_result['key_reason']} "
                    f"| WinRate: {vec_result['win_rate']:.0f}% | AvgR: {vec_result['avg_r']:.2f}R "
                    f"| Confidence: {vec_result['confidence']:.1%}"
                )
                # Store current geometry for future training (outcome will be stamped back by retraining loop)
                import uuid
                sig_id_for_vec = str(uuid.uuid4())[:12]
                self.visual_vector_engine.store_embedding(
                    signal_id=sig_id_for_vec,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    symbol=symbol,
                    pattern=setup.get('pattern_type', 'SMC_SETUP'),
                    direction=setup['direction'],
                    outcome='PENDING',  # Retraining loop stamps final outcome
                    realized_r=0.0,
                    pnl=0.0,
                    vector=query_vec,
                    session=killzone or 'UNKNOWN',
                    notes=f"density={liq_density:.1f} | shadow_score={shadow_score:.1f}",
                    regime_type=active_regime,
                    hurst=float(setup.get('hurst', 0.50)),
                    atr_percentile=float(setup.get('atr_percentile', 50.0))
                )
            except Exception as vve_err:
                logger.debug(f"Visual vector evaluation skipped (non-blocking): {vve_err}")

            # ── HIGHER-TIMEFRAME (1H) MACRO TREND & REGIME GATE ──
            # Replaces the old synthetic ETH/BTC ratio with direct 1H EMA & market structure alignment.
            # Trend-aligned setups require standard conviction (>= 7.5); counter-trend setups require high conviction (>= 8.5).
            is_counter_regime = False
            regime_msg = "1H Neutral Structure"

            # Strategy 5 (Gold 50% CE FVG) Longs and verified institutional sweep setups
            # (Turtle Soup, Judas Sniper, Silver Bullet, FVG 50% CE) with orderflow absorption or SMT
            # are designed to fade manipulation extremes and are exempt from counter-regime penalties.
            is_institutional_sweep = (
                any(p in pattern_type for p in ["TURTLE_SOUP", "JUDAS", "FVG", "50PCT", "STRAT_5", "SILVER_BULLET", "SWEEP", "REVERSAL"])
                or setup.get('cvd_absorption', False)
                or (setup.get('smt_divergence') is not None)
                or (float(setup.get('smt_strength', 0.0)) >= 1.0)
            )

            if not is_institutional_sweep:
                try:
                    if df_1h is not None and len(df_1h) >= 20:
                        ema50_1h = df_1h['close'].ewm(span=min(50, len(df_1h))).mean().iloc[-1]
                        current_close_1h = df_1h['close'].iloc[-1]
                        if current_close_1h < ema50_1h:
                            # 1H Bearish Trend: Shorts are aligned, Longs are counter-trend
                            if setup['direction'] == "LONG":
                                is_counter_regime = True
                                regime_msg = "1H Bearish Trend (Counter-Trend Long)"
                            else:
                                regime_msg = "1H Bearish Trend (Aligned Short)"
                        elif current_close_1h > ema50_1h:
                            # 1H Bullish Trend: Longs are aligned, Shorts are counter-trend
                            if setup['direction'] == "SHORT":
                                is_counter_regime = True
                                regime_msg = "1H Bullish Trend (Counter-Trend Short)"
                            else:
                                regime_msg = "1H Bullish Trend (Aligned Long)"
                except Exception as htf_err:
                    logger.debug(f"HTF Trend alignment check fallback: {htf_err}")
            else:
                regime_msg = f"Institutional Sweep / Absorption Entry (Regime Exempt: {pattern_type})"

            ai_validator_threshold = 8.5 if is_counter_regime else getattr(Config, 'AI_VALIDATOR_MIN_SCORE', 7.5)
            
            # ── PRE-COMPUTED ZERO-LATENCY AI RAG CONFLUENCE GATE ──
            dynamic_risk_mult = 1.0
            try:
                from src.engines.ai_permission_map import AIPermissionMap
                ai_approved, dynamic_risk_mult, perm_msg = AIPermissionMap.evaluate_confluence(symbol, setup['direction'], pattern_type)
                logger.info(f"🧠 [AI Permission Gate] {symbol} {setup['direction']}: {perm_msg} (Risk Mult: {dynamic_risk_mult}x)")
                if not ai_approved:
                    shadow_score = min(shadow_score, 6.0) # Suppress score below live threshold
                else:
                    if not getattr(Config, 'VARIANT_SIZING_SHADOW_MODE', True):
                        lots = round(lots * dynamic_risk_mult, 4)
                    else:
                        logger.info(f"🛡️ [VARIANT SIZING SHADOW MODE] Live sizing locked flat (1.0x). Variant mult ({dynamic_risk_mult}x) routed to Shadow Tournament.")
            except Exception as perm_eval_err:
                logger.debug(f"AI Permission Map lookup error: {perm_eval_err}")
                ai_approved = True
                dynamic_risk_mult = 1.0

            # ── LOCAL APPLE SILICON MLX LORA CONFLUENCE BOOSTER ──
            local_llm_result = None
            try:
                from src.engines.local_llm_handler import LocalLLMHandler
                local_llm = LocalLLMHandler()
                if local_llm.is_available():
                    local_scoring = local_llm.score_setup(
                        setup={
                            "symbol": symbol,
                            "pattern": pattern_type,
                            "direction": setup['direction'],
                            "bias": setup.get('trend', setup.get('direction', 'UNKNOWN')),
                            "entry": entry_price,
                            "stop_loss": sl_price,
                            "atr_percentile": round(setup.get('atr_percentile', 50.0), 1),
                            "relative_volume": round(setup.get('vol_mult', setup.get('volume_mult', 1.0)), 2),
                            "smt_confluence": "Confirmed Divergence" if setup.get('smt_divergence') else None,
                            "smt_strength": float(setup.get('smt_strength', 0.0)),
                            "cvd_absorption": setup.get('cvd_absorption', False),
                            "discount_pct": setup.get('discount_pct', None)
                        },
                        hurst=float(setup.get('hurst', 0.5)),
                        session_info={"name": killzone}
                    )
                    local_llm_result = local_scoring
                    loc_score = float(local_scoring.get("score", 0.0))
                    loc_verdict = str(local_scoring.get("verdict", "UNKNOWN"))
                    loc_provider = str(local_scoring.get("provider", local_llm.active_provider))

                    if loc_score >= 7.5 and loc_verdict == "FLOW_GO":
                        lora_boost = 2.0
                        shadow_score = min(10.0, round(shadow_score + lora_boost, 1))
                        logger.info(f"🤖 [MLX LoRA Confluence Booster] {symbol} {setup['direction']} APPROVED by {loc_provider} (Score: {loc_score:.1f}/10) -> +{lora_boost:.1f} Boost applied -> New Score: {shadow_score:.1f}/10")
                    elif loc_score < 5.0 or loc_verdict == "REJECTED":
                        logger.info(f"🤖 [MLX LoRA Guardrail] {symbol} {setup['direction']} REJECTED by {loc_provider} (Score: {loc_score:.1f}/10). Zero boost.")
                    else:
                        logger.info(f"🤖 [MLX LoRA Neutral] {symbol} {setup['direction']} Scored: {loc_score:.1f}/10 ({loc_verdict}).")
            except Exception as mlx_boost_err:
                logger.debug(f"Local MLX LoRA confluence boost fallback: {mlx_boost_err}")

            # ── 👻 SHADOW VISUAL VECTOR TELEMETRY (100% ZERO CAPITAL RISK) ──
            # Relegated to pure shadow telemetry. Does NOT mutate live shadow_score.
            vec_rec = vec_result.get('recommendation', 'NEUTRAL')
            vec_mod = float(vec_result.get('score_modifier', 0.0))
            setup['shadow_visual_vector'] = vec_result
            setup['shadow_vec_mod'] = vec_mod
            if vec_mod != 0.0:
                logger.info(f"👻 [Shadow Visual Vector Telemetry] {symbol} {setup['direction']}: Advisory modifier {vec_mod:+.1f} ({vec_rec}) logged to shadow telemetry (0% live impact).")

            passed_ai_validator = (shadow_score >= ai_validator_threshold) and ai_approved
            
            # If Strategy 5 Gold Longs are graduated and active, exempt XAU/USD from blanket shadow quarantine
            is_strat_5_gold_live = (
                symbol in ["XAU/USD", "XAUUSD", "GOLD"]
                and setup.get('direction') == "LONG"
                and any(p in pattern_type for p in ["STRAT_5", "FVG", "50PCT", "CE"])
                and getattr(Config, 'STRATEGY_5_AUTO_EXECUTE', False)
                and not setup.get('is_shadow_only', False)
            )
            is_symbol_shadow = (is_shadow or (symbol in getattr(Config, 'SHADOW_SYMBOLS', []))) and not is_strat_5_gold_live

            is_auction_or_ce = (
                "FVG" in pattern_type 
                or "50PCT" in pattern_type 
                or "STRAT_5" in pattern_type
                or "AVWAP" in pattern_type
                or "WYCKOFF" in pattern_type
                or "AMT" in pattern_type
            )
            is_low_density_sweep = (not is_dense_liq) and (not is_auction_or_ce)

            authorized_live_patterns = ["TURTLE_SOUP_LIQUIDITY_SWEEP", "LONDON_CLOSE_SILVER_BULLET"]
            if getattr(Config, 'STRATEGY_9_AUTO_EXECUTE', False):
                authorized_live_patterns.extend(["JUDAS_INDUCEMENT_SNIPER", "STRATEGY_9_JUDAS_INDUCEMENT"])
            if getattr(Config, 'STRATEGY_5_AUTO_EXECUTE', False):
                authorized_live_patterns.extend([
                    "STRAT_5_XAU_GOLD_50PCT_CE_LONG",
                    "FVG_50PCT_CE_REVERSAL_LONG",
                    "FVG_50PCT_CE_REVERSAL",
                    "FVG_CONSEQUENT_ENCROACHMENT",
                    "FVG_50PCT_CE_REVERSAL_SHADOW"
                ])
            if getattr(Config, 'AUCTION_MARKET_AUTO_EXECUTE', True):
                authorized_live_patterns.extend([
                    "AVWAP_2SIGMA_BEARISH_SNAPBACK",
                    "AVWAP_2SIGMA_BULLISH_SNAPBACK",
                    "WYCKOFF_VSA_SPRING",
                    "WYCKOFF_VSA_UPTHRUST",
                    "AMT_VALUE_AREA_HIGH_REJECTION",
                    "AMT_VALUE_AREA_LOW_REJECTION"
                ])

            is_archetype_shadow = (
                (pattern_type not in authorized_live_patterns)
                or setup.get('is_shadow_only', False)
                or (killzone == "NY_AFTERNOON_SHADOW")
                or is_symbol_shadow
                or is_low_density_sweep  # Hard density gate: noise sweeps → shadow only
            )
            is_shadow_strategy = is_archetype_shadow or (not passed_ai_validator)
            
            ai_score_val = shadow_score
            verdict_str = "SHADOW_OBSERVATION" if is_shadow_strategy else "CONFIRMED"
            
            if is_symbol_shadow:
                tag_label = "shadow asset quarantine ($0 live risk)"
                pattern_str = f"[👻 SHADOW LAB - {symbol}] {base_pattern_str}"
                ai_reasoning = f"[👻 SHADOW LAB ({symbol} $0 RISK)] {pattern_type.replace('_', ' ')} of HTF level {setup['level']:.2f}. Hurst: {setup['hurst']:.3f} ({setup['regime']}). AI Score: {shadow_score:.1f}/10. Tracking shadow expectancy..."
            elif pattern_type not in authorized_live_patterns:
                tag_label = "shadow archetype quarantine"
                pattern_str = f"[👻 SHADOW LAB - UNGRADUATED ARCHETYPE] {base_pattern_str}"
                ai_reasoning = f"[👻 SHADOW LAB (UNGRADUATED ARCHETYPE)] {pattern_type.replace('_', ' ')} on {symbol} is an ungraduated shadow archetype ($0 live capital risk). AI Score: {shadow_score:.1f}/10."
            elif is_low_density_sweep:
                tag_label = "shadow trade, low-density noise sweep"
                pattern_str = f"[👻 SHADOW - LOW DENSITY SWEEP ({liq_density:.1f}/10)] {base_pattern_str}"
                ai_reasoning = f"[👻 SHADOW LAB (LOW DENSITY SWEEP)] {pattern_type.replace('_', ' ')} of level {setup['level']:.2f} has insufficient stop cluster density ({liq_density:.1f}/10 < 6.0). Not a verified institutional POI. Quarantined to $0 risk."
            elif killzone == "NY_AFTERNOON_SHADOW" or killzone not in ["LONDON_OPEN", "LONDON_CLOSE_NY_MORNING", "ASIAN_SESSION_JUDAS", "NEW_YORK_AM", "LONDON_CLOSE"]:
                tag_label = "shadow session quarantine"
                pattern_str = f"[👻 SHADOW - SESSION RESTRICTED] {base_pattern_str}"
                ai_reasoning = f"[👻 SHADOW LAB (SESSION RESTRICTED)] {pattern_type.replace('_', ' ')} on {symbol} is shadow-only during {killzone} (Live execution restricted to London/NY/Asian). AI Score: {shadow_score:.1f}/10."
            elif setup.get('is_shadow_only', False):
                tag_label = "shadow lab quarantine"
                pattern_str = f"[👻 SHADOW LAB] {base_pattern_str}"
                ai_reasoning = f"[👻 SHADOW LAB ($0 RISK)] {pattern_type.replace('_', ' ')} on {symbol} is quarantined in Shadow Lab ($0 live capital risk). AI Score: {shadow_score:.1f}/10."
            elif is_archetype_shadow:
                tag_label = "shadow archetype quarantine"
                pattern_str = f"[👻 SHADOW LAB] {base_pattern_str}"
                ai_reasoning = f"[👻 SHADOW LAB ($0 RISK)] {pattern_type.replace('_', ' ')} of HTF level {setup['level']:.2f}. Hurst: {setup['hurst']:.3f} ({setup['regime']}). AI Score: {shadow_score:.1f}/10."
            elif not passed_ai_validator:
                if is_counter_regime:
                    tag_label = "shadow trade, counter-regime smt quarantine"
                    pattern_str = f"[👻 SHADOW - COUNTER-REGIME SMT] {base_pattern_str}"
                    ai_reasoning = f"[👻 SHADOW LAB (COUNTER-REGIME SMT)] {symbol} {setup['direction']} requires Unicorn score >= 9.0/10 during {regime_msg} (Score: {shadow_score:.1f}/10). Quarantined to $0 risk."
                elif shadow_score < ai_validator_threshold:
                    tag_label = "shadow trade, didn't pass ai validator"
                    pattern_str = f"[👻 SHADOW - DIDN'T PASS AI VALIDATOR] {base_pattern_str}"
                    cvd_detail = shadow_report.get('cvd_absorption', {}).get('details', 'No CVD')
                    vwap_z = shadow_report.get('session_vwap', {}).get('z_score', 0)
                    kalman_st = shadow_report.get('kalman_mss', {}).get('state', 'NEUTRAL')
                    ai_reasoning = f"[👻 SHADOW TRADE - DIDN'T PASS AI VALIDATOR (Score: {shadow_score:.1f}/10 < {ai_validator_threshold})] {pattern_type.replace('_', ' ')} of HTF level {setup['level']:.2f}. Failed confluences: CVD={cvd_detail}, VWAP_Z={vwap_z:.2f}, Kalman={kalman_st}."
                else:
                    tag_label = "shadow trade, ai permission gate"
                    pattern_str = f"[👻 SHADOW - AI PERMISSION GATE] {base_pattern_str}"
                    ai_reasoning = f"[👻 SHADOW TRADE - AI PERMISSION GATE ({perm_msg})] {pattern_type.replace('_', ' ')} of HTF level {setup['level']:.2f} (Score: {shadow_score:.1f}/10 >= {ai_validator_threshold})."
            else:
                tag_label = "live master weapon"
                pattern_str = base_pattern_str
                ai_reasoning = f"[👑 LIVE MASTER WEAPON] {pattern_type.replace('_', ' ')} of HTF level {setup['level']:.2f}. Hurst: {setup['hurst']:.3f} ({setup['regime']}). AI Score: {shadow_score:.1f}/10. 🔮 Vec: {vec_result['recommendation']} ({vec_result['win_rate']:.0f}% WR, {vec_result['avg_r']:.1f}R avg)."
            
            # Prepare scan payload
            scan_payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "pattern": pattern_str,
                "bias": setup.get('trend', setup.get('direction', 'UNKNOWN')),
                "direction": setup['direction'],
                "verdict": verdict_str,
                "shadow_regime": setup.get('regime', 'UNKNOWN'),
                "shadow_multiplier": 1.0,
                "session": killzone,
                "killzone": killzone,
                "hurst": setup['hurst'],
                "smt_strength": 0.0,
                "formations": f"Sweep of {setup['level']:.2f} | AI Score: {shadow_score:.1f}/10 | {tag_label}" + (f" | Confluence: {', '.join(boost_reasons)}" if boost_reasons else ""),
                # ── Visual Vector Shadow Metadata (for future gate validation) ──
                "vec_recommendation": vec_result.get('recommendation', 'NEUTRAL'),
                "vec_win_rate": round(vec_result.get('win_rate', 50.0), 1),
                "vec_avg_r": round(vec_result.get('avg_r', 0.0), 2),
                "vec_confidence": round(vec_result.get('confidence', 0.0), 3),
                "vec_key_reason": vec_result.get('key_reason', 'N/A'),
                # ── Heatmap metadata ──
                "liq_density": round(liq_density, 1),
                "is_dense_liq": is_dense_liq,
            }
            
            ai_result = {
                "score": ai_score_val,
                "reasoning": ai_reasoning
            }
            
            # Log to local SQLite & Sync to Supabase
            try:
                log_scan(scan_payload, ai_result)
            except Exception as e:
                logger.error(f"Error logging scan to DB: {e}")

            # ── Shadow Challenger: Local Bayesian Pivot (Apple Silicon MLX LoRA / Ollama) ─────────────
            try:
                from src.engines.local_llm_handler import LocalLLMHandler
                local_llm = LocalLLMHandler()
                if local_llm.is_available():
                    provider_tag = "MLX" if local_llm.active_backend == "mlx" else "OLLAMA"
                    variant_key = f"CHALLENGER_LOCAL_{provider_tag}"

                    if local_llm_result is not None:
                        local_scoring = local_llm_result
                    else:
                        local_scoring = local_llm.score_setup(
                            setup={
                                "symbol": symbol,
                                "pattern": pattern_str,
                                "direction": setup['direction'],
                                "bias": setup.get('trend', setup.get('direction', 'UNKNOWN')),
                                "entry": entry_price,
                                "stop_loss": sl_price,
                                "atr_percentile": round(setup.get('atr_percentile', 50.0), 1),
                                "relative_volume": round(setup.get('vol_mult', setup.get('volume_mult', 1.0)), 2),
                                "smt_confluence": "Confirmed Divergence" if setup.get('smt_divergence') else None,
                                "smt_strength": float(setup.get('smt_strength', 0.0)),
                                "cvd_absorption": setup.get('cvd_absorption', False),
                                "discount_pct": setup.get('discount_pct', None)
                            },
                            hurst=float(setup.get('hurst', 0.5)),
                            session_info={"name": killzone}
                        )
                    local_score = float(local_scoring.get("score", 0.0))
                    local_verdict = str(local_scoring.get("verdict", "UNKNOWN"))
                    local_reason = str(local_scoring.get("reasoning", ""))
                    local_provider = str(local_scoring.get("provider", local_llm.active_provider))
                    logger.info(f"🤖 [SHADOW TOURNAMENT: {local_provider}] {symbol} {setup['direction']} -> Score: {local_score:.1f}/10 | Verdict: {local_verdict} | {local_reason}")

                    self.counterfactual_tracker.register_shadow_trade(
                        setup={
                            "symbol": symbol,
                            "direction": setup['direction'],
                            "pattern": f"[{variant_key}] {pattern_str}",
                            "price": entry_price,
                            "stop_loss": sl_price,
                            "take_profit": tp_price,
                            "regime": setup.get('regime', 'UNKNOWN'),
                            "hurst": float(setup.get('hurst', 0.5))
                        },
                        account_key=variant_key,
                        strategy_mode=variant_key,
                        rejection_reasons=[
                            f"LOCAL_SCORE_{local_score:.1f}",
                            f"VERDICT_{local_verdict}",
                            f"CLOUD_SCORE_{shadow_score:.1f}"
                        ]
                    )
            except Exception as local_challenger_err:
                logger.debug(f"Local LLM Shadow Challenger non-blocking warning: {local_challenger_err}")
                
            # Register in Counterfactual Database for 30-Day Shadow Lab Analytics
            if is_shadow_strategy:
                setup['is_shadow_only'] = True
                acct_label = f"{pattern_type}_SHADOW"
                if not passed_ai_validator:
                    rejection_reasons = ["SHADOW_TRADE_DIDNT_PASS_AI_VALIDATOR"]
                elif is_low_density_sweep:
                    rejection_reasons = [f"LOW_DENSITY_LIQUIDITY_SWEEP_{liq_density:.1f}/10"]
                else:
                    rejection_reasons = ["SHADOW_STRATEGY_ZERO_LIVE_CAPITAL_RISK"]
                # Append visual vector recommendation for future analysis
                rejection_reasons.append(f"VEC_{vec_result.get('recommendation', 'NEUTRAL')}_{vec_result.get('win_rate', 50.0):.0f}pct_WR")
                try:
                    self.counterfactual_tracker.register_shadow_trade(
                        setup={
                            "symbol": symbol,
                            "direction": setup['direction'],
                            "pattern": pattern_str,
                            "price": entry_price,
                            "stop_loss": sl_price,
                            "take_profit": tp_price
                        },
                        account_key=acct_label,
                        strategy_mode="SHADOW_LAB_QUARANTINE",
                        rejection_reasons=rejection_reasons
                    )
                    logger.info(f"👻 {acct_label} registered in counterfactual database for {symbol} {setup['direction']} (ZERO LIVE RISK)")
                except Exception as shadow_err:
                    logger.warning(f"Failed to register shadow trade: {shadow_err}")
                
            # Auto-Execution Tranche 1 Probe (50% scale / ~0.20% fleet risk)
            exec_result = None
            if not is_shadow_strategy and getattr(Config, 'LIVE_AUTO_EXECUTION', False) and ai_score_val >= getattr(Config, 'AUTO_EXECUTION_MIN_SCORE', 8.0):
                # Anti-stacking: check for existing open positions across fleet
                has_active_pos = False
                try:
                    open_pos = self.tl.get_open_positions()
                    pos_list = open_pos if isinstance(open_pos, list) else (open_pos.get("positions", []) if isinstance(open_pos, dict) else [])
                    inst_id = str(self.tl.resolve_instrument_id(symbol))
                    sym_clean = symbol.replace("/", "").upper()
                    has_active_pos = any(
                        str(p.get("symbol", "")).replace("/", "").upper() == sym_clean or str(p.get("tradableInstrumentId")) == inst_id
                        for p in pos_list
                    )
                except Exception:
                    has_active_pos = False

                if has_active_pos:
                    logger.info(f"⚡ [PROBE & SCALE] Active position already open for {symbol}. Skipping duplicate auto-execution.")
                else:
                    exec_side = "buy" if setup['direction'].upper() == "LONG" else "sell"
                    is_probe = setup.get('is_probe_sizing', False)
                    probe_scale = getattr(Config, 'AUCTION_MARKET_PROBE_RISK_SCALE', 0.50) if is_probe else getattr(Config, 'AUTO_PROBE_RISK_SCALE', 1.00)
                    tranche_tag = "AUCTION_PROBE_ENTRY" if is_probe else "FULL_SIZE_ENTRY"
                    logger.info(f"⚡ [AUTO-EXECUTION] Auto-executing {tranche_tag} (scale: {probe_scale:.2f}x) on {symbol} {exec_side.upper()} @ {entry_price}...")
                    try:
                        exec_result = self.tl.execute_trade_across_all_accounts(
                            symbol=symbol,
                            side=exec_side,
                            entry_price=entry_price,
                            stop_loss=sl_price,
                            take_profit=tp_price,
                            risk_scale=probe_scale,
                            tranche_label=tranche_tag,
                            ai_score=ai_score_val,
                            has_smt=bool(setup.get('smt_strength', 0.0) > 0 or setup.get('has_smt_divergence', False)),
                            session=killzone,
                            hurst_exponent=float(setup.get('hurst', setup.get('hurst_exponent', 0.58)))
                        )
                        # Record in active trade brackets cache
                        if not hasattr(self, '_active_trade_brackets'):
                            self._active_trade_brackets = {}
                        sym_key = symbol.replace("/", "").upper()
                        self._active_trade_brackets[sym_key] = {
                            'symbol': symbol,
                            'side': exec_side,
                            'entry_price': float(entry_price),
                            'stop_loss': float(sl_price),
                            'take_profit': float(tp_price),
                            'initial_r_dist': abs(float(entry_price) - float(sl_price)),
                            'session': killzone,
                            'entry_time': datetime.now(timezone.utc),
                            'tier': 0
                        }
                        # Register in Execution Strategy Shadow Tournament
                        if hasattr(self, 'exec_shadow_engine'):
                            self.exec_shadow_engine.register_trade(
                                symbol=symbol,
                                direction=exec_side,
                                entry_price=float(entry_price),
                                stop_loss=float(sl_price),
                                take_profit=float(tp_price),
                                risk_usd=129.0,
                                variant_mult=float(dynamic_risk_mult)
                            )
                    except Exception as exec_err:
                        logger.error(f"Error auto-executing trade: {exec_err}")
                
            # Send Telegram Alert
            try:
                is_auto_filled = exec_result and exec_result.get('success')
                alert_phase = "SHADOW_OBSERVATION" if is_shadow_strategy else ("AUTO_EXECUTED" if is_auto_filled else "EXECUTION")
                buttons = None
                    
                exec_notice = f"\n\n⚡ <b>AUTO-EXECUTED:</b> 100% Position filled across {exec_result.get('filled_count', 0)}/{exec_result.get('total_accounts', 0)} accounts with SL & TP attached!" if is_auto_filled else ""
                
                send_alert(
                    symbol=symbol,
                    timeframe="5m",
                    pattern=pattern_str,
                    ai_score=ai_score_val,
                    reasoning=f"{ai_result['reasoning']}\n🎯 <b>Liquidity Context:</b> {liq_msg if 'liq_msg' in locals() else 'Standard SMC Level'}{' (⚠️ ZERO LIVE CAPITAL RISK - Shadow Tracking Only)' if is_shadow_strategy else ''}{exec_notice}",
                    verdict=verdict_str,
                    session_info={"name": killzone, "phase": alert_phase},
                    bias_data={"daily": setup.get('trend', setup.get('direction', 'UNKNOWN')), "htf": setup.get('trend', setup.get('direction', 'UNKNOWN')), "dxy_trend": "N/A"},
                    liquidity_targets={"target_price": tp_price, "target_type": "DENSE_STOP_CLUSTER", "distance_pips": setup.get('sweep_dist', 0.0)},
                    risk_calc={
                        "entry": entry_price,
                        "stop_loss": sl_price,
                        "position_size": round(lots * (getattr(Config, 'AUTO_PROBE_RISK_SCALE', 1.00) if is_auto_filled else 1.0), 2),
                        "take_profit": tp_price,
                        "position_value": position_value
                    },
                    buttons=buttons
                )
            except Exception as e:
                logger.error(f"Error sending Telegram alert: {e}")
                
            return setup
            
        return None

    def check_and_trail_positions(self):
        """
        Active Risk Watchdog with Tiered Profit Protection & Session Transition Lock:
        Monitors open fleet positions every cycle.
        
        Tier 1 (+1.5R):
          Automatically moves Stop Loss to Entry Price (Break-Even) via in-place PATCH.
          Dispatches Telegram alert confirming trade is 100% risk-free.
          
        Tier 2 (+2.5R or >= 80% TP distance):
          Automatically trails Stop Loss to +1.0R in profit (Guaranteed Green).
          Dispatches Telegram alert confirming profit lock.
          
        Session Transition Lock:
          When holding a trade from Asian session into the London Open transition,
          if floating profit >= +1.0R, proactively moves SL to Break-Even.
        """
        try:
            open_pos = self.tl.get_open_positions()
            if not open_pos:
                return

            if not hasattr(self, '_active_trade_brackets'):
                self._active_trade_brackets = {}
            if not hasattr(self, '_position_tiers'):
                self._position_tiers = {}

            now_utc = datetime.now(timezone.utc)
            utc_float = now_utc.hour + (now_utc.minute / 60.0)
            is_london_transition = (6.75 <= utc_float <= 7.25) or (now_utc.hour == 7 and now_utc.minute <= 15)

            be_trigger_r = getattr(Config, 'BE_TRIGGER_R', 1.5)
            tier2_trigger_r = getattr(Config, 'TIER2_LOCK_TRIGGER_R', 2.5)
            tier2_locked_r = getattr(Config, 'TIER2_LOCK_LOCKED_R', 1.0)
            tier2_tp_pct = getattr(Config, 'TIER2_TP_PCT_TRIGGER', 0.80)
            session_protect = getattr(Config, 'SESSION_TRANSITION_PROTECT_ENABLED', True)
            session_min_r = getattr(Config, 'SESSION_TRANSITION_MIN_R', 1.0)

            for p in open_pos:
                pos_id = str(p.get("id", ""))
                if not pos_id:
                    continue

                symbol = p.get("symbol", "")
                sym_clean = symbol.replace("/", "").upper()
                side = str(p.get("side", "")).lower()
                entry_price = float(p.get("price") or p.get("avgPrice") or 0.0)

                # Recover bracket metadata if available
                bracket_info = self._active_trade_brackets.get(sym_clean, {})
                cached_sl = bracket_info.get('stop_loss', 0.0)
                cached_tp = bracket_info.get('take_profit', 0.0)
                cached_r_dist = bracket_info.get('initial_r_dist', 0.0)
                cached_session = bracket_info.get('session', '')

                current_sl = float(p.get("stopLoss") or cached_sl or 0.0)
                target_tp = float(p.get("takeProfit") or cached_tp or 0.0)

                if entry_price <= 0:
                    continue

                initial_r_dist = cached_r_dist if cached_r_dist > 0 else abs(entry_price - current_sl)
                # Plausible stop floor: if SL has already trailed or is uninitialized, fall back to minimum ATR stop distance
                min_stop_pct = Config.MIN_STOP_PCT.get(symbol, 0.003)
                if initial_r_dist < (entry_price * min_stop_pct * 0.5):
                    initial_r_dist = entry_price * min_stop_pct

                # Fetch live mark price with symbol normalization & broker fallback
                fetch_sym = symbol
                if "/" not in fetch_sym:
                    if "BTC" in fetch_sym: fetch_sym = "BTC/USD"
                    elif "ETH" in fetch_sym: fetch_sym = "ETH/USD"
                    elif "SOL" in fetch_sym: fetch_sym = "SOL/USD"
                    elif "XAU" in fetch_sym: fetch_sym = "XAU/USD"
                    elif len(fetch_sym) == 6: fetch_sym = f"{fetch_sym[:3]}/{fetch_sym[3:]}"

                df_tick = self.fetch_data(fetch_sym, '5m', limit=2, synchronized=False)
                current_price = None
                if df_tick is not None and len(df_tick) > 0:
                    try:
                        current_price = float(df_tick.iloc[-1]['close'])
                    except Exception:
                        current_price = None

                # Broker-native mark price fallback from PnL if exchange fetch fails
                if current_price is None or current_price <= 0:
                    pnl = float(p.get('pnl') or 0.0)
                    qty = float(p.get('qty') or 0.0)
                    contract_size = Config.get_contract_size(fetch_sym)
                    if qty > 0:
                        delta_price = pnl / (qty * contract_size)
                        current_price = (entry_price + delta_price) if side == "buy" else (entry_price - delta_price)

                if current_price is None or current_price <= 0:
                    continue

                if hasattr(self, 'exec_shadow_engine'):
                    self.exec_shadow_engine.update_price(symbol, current_price)

                # Calculate floating gain and R-multiple
                floating_gain = (current_price - entry_price) if side == "buy" else (entry_price - current_price)
                current_r = floating_gain / initial_r_dist

                # Calculate progress to TP if TP is known
                tp_dist = abs(entry_price - target_tp) if target_tp > 0 else (initial_r_dist * 3.0)
                progress_to_tp = (floating_gain / tp_dist) if tp_dist > 0 else 0.0

                current_tier = self._position_tiers.get(pos_id, 0)

                # ── Tier 2 Check: +2.5R or >= 80% TP Progress ─────────────
                if (current_r >= tier2_trigger_r or progress_to_tp >= tier2_tp_pct) and current_tier < 2:
                    locked_price = entry_price + (tier2_locked_r * initial_r_dist) if side == "buy" else entry_price - (tier2_locked_r * initial_r_dist)
                    logger.info(f"💰 [TIER 2 PROFIT LOCK] {symbol} {side.upper()} reached +{current_r:.2f}R ({progress_to_tp*100:.1f}% to TP). Trailing Stop Loss to +{tier2_locked_r:.1f}R in profit (${locked_price:.2f})...")
                    
                    self.tl.update_fleet_stop_loss(new_stop_loss=locked_price, symbol=symbol)
                    self._position_tiers[pos_id] = 2
                    if sym_clean in self._active_trade_brackets:
                        self._active_trade_brackets[sym_clean]['tier'] = 2

                    try:
                        from src.clients.telegram_notifier import TelegramNotifier
                        tn = TelegramNotifier()
                        tn._send_message(
                            f"💰 <b>TIER 2 PROFIT LOCK ACTIVATED!</b>\n\n"
                            f"Asset: <b>{symbol}</b> ({side.upper()})\n"
                            f"Current Profit: <b>+{current_r:.2f}R</b> (${current_price:.2f})\n"
                            f"Progress to TP: <b>{progress_to_tp*100:.1f}%</b>\n"
                            f"Stop Loss Trailed: <b>+1.0R in profit (${locked_price:.2f})</b>\n\n"
                            f"🔒 <b>Status:</b> Position is locked in the green! Runner continuing to full TP. 🚀"
                        )
                    except Exception as tg_err:
                        logger.warning(f"Error sending Tier 2 alert to Telegram: {tg_err}")
                    continue

                # ── Tier 1 Check: +1.5R True Net Break-Even Lock ──────────
                elif current_r >= be_trigger_r and current_tier < 1:
                    be_offset_r = getattr(Config, 'BE_OFFSET_R', 0.08)
                    min_usd_map = getattr(Config, 'BE_MIN_OFFSET_USD', {"BTC": 25.0, "ETH": 2.0, "SOL": 0.20, "XAU": 0.80})
                    min_usd = 0.0
                    for k, v in min_usd_map.items():
                        if k in sym_clean:
                            min_usd = v
                            break
                    fee_buffer = max(be_offset_r * initial_r_dist, min_usd)
                    net_be_price = round(entry_price + fee_buffer, 2) if side == "buy" else round(entry_price - fee_buffer, 2)

                    logger.info(f"🛡️ [BREAK-EVEN WATCHDOG] {symbol} {side.upper()} reached +{current_r:.2f}R (>= {be_trigger_r}R). Trailing Stop Loss to True Net Break-Even (${net_be_price:.2f}, +${fee_buffer:.2f} fee buffer)...")
                    
                    self.tl.update_fleet_stop_loss(new_stop_loss=net_be_price, symbol=symbol)
                    self._position_tiers[pos_id] = 1
                    if sym_clean in self._active_trade_brackets:
                        self._active_trade_brackets[sym_clean]['tier'] = 1

                    try:
                        from src.clients.telegram_notifier import TelegramNotifier
                        tn = TelegramNotifier()
                        tn._send_message(
                            f"🛡️ <b>TRUE NET BREAK-EVEN LOCK ACTIVATED!</b>\n\n"
                            f"Asset: <b>{symbol}</b> ({side.upper()})\n"
                            f"Current Profit: <b>+{current_r:.2f}R</b> (${current_price:.2f})\n"
                            f"Stop Loss: Trailed to Net BE <b>${net_be_price:.2f}</b> (+${fee_buffer:.2f} commission & spread buffer)\n\n"
                            f"✅ <b>Status:</b> Position is now <b>100% IMMUNE TO BROKER FEES</b>! Runner tracking to full TP. 🚀"
                        )
                    except Exception as tg_err:
                        logger.warning(f"Error sending BE alert to Telegram: {tg_err}")
                    continue

                # ── Session Transition Check: Asian position into London Open ───
                elif session_protect and is_london_transition and current_r >= session_min_r and current_tier == 0:
                    if "ASIA" in cached_session.upper() or cached_session == "ASIAN_SESSION_JUDAS":
                        be_offset_r = getattr(Config, 'BE_OFFSET_R', 0.08)
                        min_usd_map = getattr(Config, 'BE_MIN_OFFSET_USD', {"BTC": 25.0, "ETH": 2.0, "SOL": 0.20, "XAU": 0.80})
                        min_usd = 0.0
                        for k, v in min_usd_map.items():
                            if k in sym_clean:
                                min_usd = v
                                break
                        fee_buffer = max(be_offset_r * initial_r_dist, min_usd)
                        net_be_price = round(entry_price + fee_buffer, 2) if side == "buy" else round(entry_price - fee_buffer, 2)

                        logger.info(f"⏰ [SESSION TRANSITION LOCK] {symbol} {side.upper()} floating +{current_r:.2f}R entering London Open. Moving Stop Loss to True Net Break-Even (${net_be_price:.2f})...")
                        
                        self.tl.update_fleet_stop_loss(new_stop_loss=net_be_price, symbol=symbol)
                        self._position_tiers[pos_id] = 1
                        if sym_clean in self._active_trade_brackets:
                            self._active_trade_brackets[sym_clean]['tier'] = 1

                        try:
                            from src.clients.telegram_notifier import TelegramNotifier
                            tn = TelegramNotifier()
                            tn._send_message(
                                f"⏰ <b>SESSION TRANSITION PROTECTION!</b>\n\n"
                                f"Asset: <b>{symbol}</b> ({side.upper()})\n"
                                f"Current Profit: <b>+{current_r:.2f}R</b> (${current_price:.2f})\n"
                                f"Reason: <b>London Open Volatility Transition</b>\n"
                                f"Stop Loss: Locked at Net BE <b>${net_be_price:.2f}</b> (+${fee_buffer:.2f} fee buffer)\n\n"
                                f"🛡️ <b>Status:</b> Asian position shielded from London opening volatility with zero fee drag! 🚀"
                            )
                        except Exception as tg_err:
                            logger.warning(f"Error sending session transition alert: {tg_err}")
                        continue
        except Exception as e:
            logger.error(f"Error in check_and_trail_positions watchdog: {e}")

