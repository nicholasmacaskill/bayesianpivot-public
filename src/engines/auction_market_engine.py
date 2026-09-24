"""
Auction Market & Quantitative Microstructure Engine
===================================================
Implements non-ICT institutional market mechanics:
1. Auction Market Theory (AMT) & Volume Profile (VAH, VAL, POC, Poor Extremes)
2. Anchored VWAP (AVWAP) with Dynamic Standard Deviation Dispersion Bands (±1σ, ±2σ, ±2.5σ)
3. Order Flow Footprint & Delta Absorption (Iceberg Limit Absorption)
4. Classical Wyckoff Volume Spread Analysis (VSA Springs & Upthrusts)

Serves two critical roles:
- Standalone Shadow Contenders in the Tournament Lab ($0 Live Capital Risk)
- Modular Confluence Enhancers for primary Bayesian Pivot / SMC setups
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple
import pandas as pd
import numpy as np

from src.core.config import Config

logger = logging.getLogger("AuctionMarketEngine")


class AuctionMarketEngine:
    """
    Quantitative Auction & Microstructure Engine.
    Evaluates market state independently of ICT killzone/SMT constraints.
    """

    def __init__(self):
        self.value_area_pct = 0.70  # Standard 70% Value Area
        self.num_profile_bins = 50   # Discretized price bins for Volume Profile
        self.sigma_threshold = 2.0   # 2.0 Standard Deviations for AVWAP overextension

    # ──────────────────────────────────────────────────────────────────────────
    # 1. AUCTION MARKET THEORY: VOLUME PROFILE & VALUE AREA
    # ──────────────────────────────────────────────────────────────────────────
    def compute_volume_profile(self, df: pd.DataFrame, lookback: int = 48) -> Dict[str, float]:
        """
        Computes the Volume Profile, Point of Control (POC), Value Area High (VAH),
        and Value Area Low (VAL) over a lookback window (e.g., 48 1H candles = 2 days).
        """
        if df is None or len(df) < 10:
            return {"poc": 0.0, "vah": 0.0, "val": 0.0, "total_vol": 0.0}

        subset = df.iloc[-lookback:].copy()
        low_min = subset["low"].min()
        high_max = subset["high"].max()
        price_range = high_max - low_min

        if price_range <= 0:
            mid = float(subset["close"].iloc[-1])
            return {"poc": mid, "vah": mid, "val": mid, "total_vol": 0.0}

        bins = np.linspace(low_min, high_max, self.num_profile_bins + 1)
        bin_volumes = np.zeros(self.num_profile_bins)

        # Distribute bar volume proportionally across the candle range
        for _, row in subset.iterrows():
            c_low = row["low"]
            c_high = row["high"]
            c_vol = row["volume"]
            if c_high == c_low:
                idx = min(self.num_profile_bins - 1, max(0, int((c_low - low_min) / price_range * self.num_profile_bins)))
                bin_volumes[idx] += c_vol
            else:
                bin_mask = (bins[1:] >= c_low) & (bins[:-1] <= c_high)
                count = np.sum(bin_mask)
                if count > 0:
                    bin_volumes[bin_mask] += (c_vol / count)

        # Point of Control (POC)
        poc_idx = int(np.argmax(bin_volumes))
        bin_mids = (bins[:-1] + bins[1:]) / 2.0
        poc = float(bin_mids[poc_idx])

        # Value Area (70% total volume around POC)
        total_vol = float(np.sum(bin_volumes))
        target_vol = total_vol * self.value_area_pct

        curr_vol = bin_volumes[poc_idx]
        up_idx = poc_idx
        down_idx = poc_idx

        while curr_vol < target_vol and (up_idx < self.num_profile_bins - 1 or down_idx > 0):
            up_vol = bin_volumes[up_idx + 1] if up_idx < self.num_profile_bins - 1 else 0.0
            down_vol = bin_volumes[down_idx - 1] if down_idx > 0 else 0.0

            if up_vol >= down_vol and up_idx < self.num_profile_bins - 1:
                up_idx += 1
                curr_vol += up_vol
            elif down_idx > 0:
                down_idx -= 1
                curr_vol += down_vol
            else:
                if up_idx < self.num_profile_bins - 1:
                    up_idx += 1
                    curr_vol += up_vol
                else:
                    break

        vah = float(bins[up_idx + 1])
        val = float(bins[down_idx])

        return {
            "poc": round(poc, 2),
            "vah": round(vah, 2),
            "val": round(val, 2),
            "total_vol": round(total_vol, 2)
        }

    def check_amt_value_rejection(self, df_5m: pd.DataFrame, df_1h: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        Detects Auction Market Theory 80% Rule Value Area Rejections.
        - Bearish: Price poked above VAH, got rejected back into the Value Area. Target: VAL.
        - Bullish: Price poked below VAL, got rejected back into the Value Area. Target: VAH.
        """
        if df_5m is None or len(df_5m) < 15 or df_1h is None or len(df_1h) < 24:
            return None

        vp = self.compute_volume_profile(df_1h, lookback=48)
        vah = vp["vah"]
        val = vp["val"]
        poc = vp["poc"]

        if vah <= 0 or val <= 0 or vah <= val:
            return None

        last = df_5m.iloc[-1]
        prev = df_5m.iloc[-2]
        close = float(last["close"])
        high = float(last["high"])
        low = float(last["low"])
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])

        # Bearish Value Rejection: poked above VAH, rejected back inside
        if (high > vah or prev_high > vah) and close < vah and close > val:
            stop_loss = max(high, prev_high) + (vah - val) * 0.05
            target = val  # 80% rule target opposite side of Value Area
            risk = stop_loss - close
            reward = close - target
            if risk > 0 and reward / risk >= 1.8:
                return {
                    "strategy_id": "STRAT_AMT_VALUE_REJECTION",
                    "pattern": "AMT_VALUE_AREA_HIGH_REJECTION",
                    "direction": "SHORT",
                    "entry": close,
                    "stop_loss": round(stop_loss, 2),
                    "target": round(target, 2),
                    "target_rr": round(reward / risk, 2),
                    "poc": poc,
                    "vah": vah,
                    "val": val,
                    "conviction": 8.0
                }

        # Bullish Value Rejection: poked below VAL, rejected back inside
        if (low < val or prev_low < val) and close > val and close < vah:
            stop_loss = min(low, prev_low) - (vah - val) * 0.05
            target = vah  # 80% rule target opposite side of Value Area
            risk = close - stop_loss
            reward = target - close
            if risk > 0 and reward / risk >= 1.8:
                return {
                    "strategy_id": "STRAT_AMT_VALUE_REJECTION",
                    "pattern": "AMT_VALUE_AREA_LOW_REJECTION",
                    "direction": "LONG",
                    "entry": close,
                    "stop_loss": round(stop_loss, 2),
                    "target": round(target, 2),
                    "target_rr": round(reward / risk, 2),
                    "poc": poc,
                    "vah": vah,
                    "val": val,
                    "conviction": 8.0
                }

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # 2. ANCHORED VWAP & STATISTICAL DISPERSION BANDS (±1σ, ±2σ, ±2.5σ)
    # ──────────────────────────────────────────────────────────────────────────
    def compute_anchored_vwap(self, df: pd.DataFrame, anchor_lookback: int = 100) -> Dict[str, float]:
        """
        Computes Anchored VWAP and standard deviation bands over an anchor lookback.
        """
        if df is None or len(df) < 20:
            return {"avwap": 0.0, "upper_1sigma": 0.0, "lower_1sigma": 0.0, "upper_2sigma": 0.0, "lower_2sigma": 0.0, "z_score": 0.0}

        subset = df.iloc[-anchor_lookback:].copy()
        typical_price = (subset["high"] + subset["low"] + subset["close"]) / 3.0
        volume = subset["volume"]

        cum_vol = volume.cumsum()
        cum_pv = (typical_price * volume).cumsum()

        cum_vol = cum_vol.replace(0, 1e-9)
        avwap_series = cum_pv / cum_vol

        cum_sq_diff = (volume * (typical_price - avwap_series) ** 2).cumsum()
        variance = cum_sq_diff / cum_vol
        std_dev_series = np.sqrt(np.maximum(0, variance))

        current_avwap = float(avwap_series.iloc[-1])
        current_sigma = float(std_dev_series.iloc[-1])
        current_close = float(subset["close"].iloc[-1])

        z_score = (current_close - current_avwap) / (current_sigma if current_sigma > 0 else 1.0)

        return {
            "avwap": round(current_avwap, 2),
            "upper_1sigma": round(current_avwap + current_sigma, 2),
            "lower_1sigma": round(current_avwap - current_sigma, 2),
            "upper_2sigma": round(current_avwap + 2.0 * current_sigma, 2),
            "lower_2sigma": round(current_avwap - 2.0 * current_sigma, 2),
            "z_score": round(float(z_score), 2)
        }

    def check_avwap_snapback(self, df_5m: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        Detects statistical overextensions to the ±2σ AVWAP bands.
        Hunts mean-reversion snapbacks to the AVWAP midline.
        """
        if df_5m is None or len(df_5m) < 30:
            return None

        vwap_data = self.compute_anchored_vwap(df_5m, anchor_lookback=100)
        z_score = vwap_data["z_score"]
        avwap = vwap_data["avwap"]
        upper_2sigma = vwap_data["upper_2sigma"]
        lower_2sigma = vwap_data["lower_2sigma"]

        last = df_5m.iloc[-1]
        close = float(last["close"])
        high = float(last["high"])
        low = float(last["low"])

        # Overextended Bullish Exhaustion (Z >= +2.0) -> Reversion Short
        if z_score >= 1.95 or high >= upper_2sigma:
            if close < upper_2sigma:
                stop_loss = high + (high - avwap) * 0.15
                target = avwap
                risk = stop_loss - close
                reward = close - target
                if risk > 0 and reward / risk >= 1.8:
                    return {
                        "strategy_id": "STRAT_AVWAP_SIGMA_SNAPBACK",
                        "pattern": "AVWAP_2SIGMA_BEARISH_SNAPBACK",
                        "direction": "SHORT",
                        "entry": close,
                        "stop_loss": round(stop_loss, 2),
                        "target": round(target, 2),
                        "target_rr": round(reward / risk, 2),
                        "z_score": z_score,
                        "avwap": avwap,
                        "conviction": 8.0
                    }

        # Overextended Bearish Exhaustion (Z <= -2.0) -> Reversion Long
        if z_score <= -1.95 or low <= lower_2sigma:
            if close > lower_2sigma:
                stop_loss = low - (avwap - low) * 0.15
                target = avwap
                risk = close - stop_loss
                reward = target - close
                if risk > 0 and reward / risk >= 1.8:
                    return {
                        "strategy_id": "STRAT_AVWAP_SIGMA_SNAPBACK",
                        "pattern": "AVWAP_2SIGMA_BULLISH_SNAPBACK",
                        "direction": "LONG",
                        "entry": close,
                        "stop_loss": round(stop_loss, 2),
                        "target": round(target, 2),
                        "target_rr": round(reward / risk, 2),
                        "z_score": z_score,
                        "avwap": avwap,
                        "conviction": 8.0
                    }

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # 3. ORDER FLOW FOOTPRINT & ICEBERG ABSORPTION
    # ──────────────────────────────────────────────────────────────────────────
    def check_delta_absorption(self, df_5m: pd.DataFrame, live_orderflow: Any = None) -> Optional[Dict[str, Any]]:
        """
        Detects aggressive taker volume hitting passive limit order iceberg absorption.
        """
        if df_5m is None or len(df_5m) < 10:
            return None

        # Check live orderflow stream if attached
        if live_orderflow is not None and hasattr(live_orderflow, "get_recent_cvd_summary"):
            try:
                cvd_summary = live_orderflow.get_recent_cvd_summary()
                if cvd_summary and cvd_summary.get("is_absorbed"):
                    direction = "SHORT" if cvd_summary.get("absorbed_buyers") else "LONG"
                    last = df_5m.iloc[-1]
                    close = float(last["close"])
                    atr = float((df_5m["high"] - df_5m["low"]).rolling(14).mean().iloc[-1])
                    stop_loss = close + (1.2 * atr) if direction == "SHORT" else close - (1.2 * atr)
                    target = close - (3.0 * atr) if direction == "SHORT" else close + (3.0 * atr)
                    return {
                        "strategy_id": "STRAT_DELTA_ABSORPTION",
                        "pattern": f"ORDERFLOW_ICEBERG_ABSORPTION_{direction}",
                        "direction": direction,
                        "entry": close,
                        "stop_loss": round(stop_loss, 2),
                        "target": round(target, 2),
                        "target_rr": 2.5,
                        "conviction": 8.5
                    }
            except Exception as e:
                logger.debug(f"Orderflow delta absorption check error: {e}")

        # Bar-level Volume Delta divergence proxy
        last = df_5m.iloc[-1]
        c_vol = float(last["volume"])
        avg_vol = float(df_5m["volume"].rolling(20).mean().iloc[-1])

        c_range = float(last["high"] - last["low"])
        upper_wick = float(last["high"] - max(last["open"], last["close"]))
        lower_wick = float(min(last["open"], last["close"]) - last["low"])

        if c_range > 0 and c_vol >= 1.8 * avg_vol:
            if upper_wick / c_range >= 0.60:
                close = float(last["close"])
                stop_loss = float(last["high"]) + (c_range * 0.1)
                target = close - (c_range * 2.5)
                return {
                    "strategy_id": "STRAT_DELTA_ABSORPTION",
                    "pattern": "BEARISH_ICEBERG_ABSORPTION_EXHAUSTION",
                    "direction": "SHORT",
                    "entry": close,
                    "stop_loss": round(stop_loss, 2),
                    "target": round(target, 2),
                    "target_rr": 2.5,
                    "conviction": 8.0
                }
            elif lower_wick / c_range >= 0.60:
                close = float(last["close"])
                stop_loss = float(last["low"]) - (c_range * 0.1)
                target = close + (c_range * 2.5)
                return {
                    "strategy_id": "STRAT_DELTA_ABSORPTION",
                    "pattern": "BULLISH_ICEBERG_ABSORPTION_EXHAUSTION",
                    "direction": "LONG",
                    "entry": close,
                    "stop_loss": round(stop_loss, 2),
                    "target": round(target, 2),
                    "target_rr": 2.5,
                    "conviction": 8.0
                }

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # 4. WYCKOFF METHOD: VOLUME SPREAD ANALYSIS (VSA) SPRINGS & UPTHRUSTS
    # ──────────────────────────────────────────────────────────────────────────
    def check_wyckoff_vsa_spring(self, df_5m: pd.DataFrame, df_1h: pd.DataFrame, symbol: str = "BTC/USD") -> Optional[Dict[str, Any]]:
        """
        Detects true Wyckoff Springs / Upthrusts (UTAD) validated by Effort vs. Result.
        - Spring: Sweeps support, but testing volume drops sharply (proving zero remaining supply).
        - Upthrust: Sweeps resistance, but testing volume drops sharply (no demand).
        Enforces MIN_STOP_PCT volatility buffer so institutional wicks don't prematurely clip stops.
        """
        if df_5m is None or len(df_5m) < 25:
            return None

        last = df_5m.iloc[-1]
        prev = df_5m.iloc[-2]
        prior_20 = df_5m.iloc[-22:-2]

        recent_support = float(prior_20["low"].min())
        recent_resistance = float(prior_20["high"].max())
        avg_vol = float(prior_20["volume"].mean())

        # Wyckoff Spring: Low poked below recent support, closed back above
        # with low/moderate testing volume (No Supply)
        if prev["low"] < recent_support and last["close"] > recent_support:
            recovery_vol = float(last["volume"])
            if recovery_vol < 1.4 * avg_vol:
                close = float(last["close"])
                min_stop_pct = getattr(Config, 'MIN_STOP_PCT', {}).get(symbol, 0.003)
                min_stop_dist = close * min_stop_pct
                buffer = max((recent_resistance - recent_support) * 0.08, min_stop_dist)
                stop_loss = float(prev["low"]) - buffer
                if (close - stop_loss) < min_stop_dist:
                    stop_loss = close - min_stop_dist
                target = recent_resistance
                risk = close - stop_loss
                reward = target - close
                if risk > 0 and reward / risk >= 1.8:
                    return {
                        "strategy_id": "STRAT_WYCKOFF_VSA_SPRING",
                        "pattern": "WYCKOFF_VSA_SPRING_NO_SUPPLY",
                        "direction": "LONG",
                        "entry": close,
                        "stop_loss": round(stop_loss, 2),
                        "target": round(target, 2),
                        "target_rr": round(reward / risk, 2),
                        "conviction": 8.5
                    }

        # Wyckoff Upthrust (UTAD): High poked above recent resistance, closed back below
        if prev["high"] > recent_resistance and last["close"] < recent_resistance:
            recovery_vol = float(last["volume"])
            if recovery_vol < 1.4 * avg_vol:
                close = float(last["close"])
                min_stop_pct = getattr(Config, 'MIN_STOP_PCT', {}).get(symbol, 0.003)
                min_stop_dist = close * min_stop_pct
                buffer = max((recent_resistance - recent_support) * 0.08, min_stop_dist)
                stop_loss = float(prev["high"]) + buffer
                if (stop_loss - close) < min_stop_dist:
                    stop_loss = close + min_stop_dist
                target = recent_support
                risk = stop_loss - close
                reward = close - target
                if risk > 0 and reward / risk >= 1.8:
                    return {
                        "strategy_id": "STRAT_WYCKOFF_VSA_SPRING",
                        "pattern": "WYCKOFF_VSA_UPTHRUST_NO_DEMAND",
                        "direction": "SHORT",
                        "entry": close,
                        "stop_loss": round(stop_loss, 2),
                        "target": round(target, 2),
                        "target_rr": round(reward / risk, 2),
                        "conviction": 8.5
                    }

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # 5. ALL CONCURRENT EVALUATION & MODULAR CONFLUENCE BOOSTERS
    # ──────────────────────────────────────────────────────────────────────────
    def evaluate_all_shadow_contenders(
        self,
        symbol: str,
        df_5m: pd.DataFrame,
        df_1h: pd.DataFrame,
        live_orderflow: Any = None
    ) -> List[Dict[str, Any]]:
        """
        Evaluates all 4 Non-ICT models for Tournament Lab tracking ($0 live risk).
        """
        candidates = []

        amt_setup = self.check_amt_value_rejection(df_5m, df_1h)
        if amt_setup:
            amt_setup["symbol"] = symbol
            candidates.append(amt_setup)

        avwap_setup = self.check_avwap_snapback(df_5m)
        if avwap_setup:
            avwap_setup["symbol"] = symbol
            candidates.append(avwap_setup)

        delta_setup = self.check_delta_absorption(df_5m, live_orderflow)
        if delta_setup:
            delta_setup["symbol"] = symbol
            candidates.append(delta_setup)

        wyckoff_setup = self.check_wyckoff_vsa_spring(df_5m, df_1h, symbol=symbol)
        if wyckoff_setup:
            wyckoff_setup["symbol"] = symbol
            candidates.append(wyckoff_setup)

        return candidates

    def get_confluence_boost(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        df_5m: pd.DataFrame,
        df_1h: pd.DataFrame,
        live_orderflow: Any = None
    ) -> Tuple[float, List[str]]:
        """
        Provides modular conviction boosters to existing Bayesian Pivot / SMC setups.
        Returns: (boost_score: float, reasons: List[str])
        """
        boost = 0.0
        reasons = []

        try:
            # A. Value Area Alignment (AMT)
            vp = self.compute_volume_profile(df_1h, lookback=48)
            vah = vp.get("vah", 0.0)
            val = vp.get("val", 0.0)
            recent_low = float(df_5m["low"].iloc[-3:].min()) if df_5m is not None and len(df_5m) >= 3 else entry_price
            recent_high = float(df_5m["high"].iloc[-3:].max()) if df_5m is not None and len(df_5m) >= 3 else entry_price

            if direction.upper() in ["SHORT", "SELL"] and vah > 0:
                # Sweep probed into/above VAH or entry is aligned with upper boundary
                if recent_high >= vah * 0.997 and entry_price >= vah * 0.990:
                    boost += 0.8
                    reasons.append(f"AMT_VAH_CONFLUENCE (+0.8, VAH: {vah:,.2f})")
            elif direction.upper() in ["LONG", "BUY"] and val > 0:
                # Sweep probed into/below VAL or entry is aligned with lower boundary
                if recent_low <= val * 1.003 and entry_price <= val * 1.010:
                    boost += 0.8
                    reasons.append(f"AMT_VAL_CONFLUENCE (+0.8, VAL: {val:,.2f})")

            # B. Anchored VWAP Sigma Alignment
            vwap_data = self.compute_anchored_vwap(df_5m, anchor_lookback=100)
            z_score = vwap_data["z_score"]
            if direction.upper() in ["SHORT", "SELL"] and z_score >= 1.5:
                boost += 0.5
                reasons.append(f"AVWAP_OVEREXTENSION (+0.5, Z-Score: {z_score:.2f})")
            elif direction.upper() in ["LONG", "BUY"] and z_score <= -1.5:
                boost += 0.5
                reasons.append(f"AVWAP_OVERSOLD (+0.5, Z-Score: {z_score:.2f})")

            # C. Delta Absorption Confirmation
            delta_setup = self.check_delta_absorption(df_5m, live_orderflow)
            if delta_setup and delta_setup["direction"] == direction.upper():
                boost += 1.0
                reasons.append(f"ORDERFLOW_ICEBERG_ABSORPTION (+1.0, {delta_setup['pattern']})")

        except Exception as e:
            logger.debug(f"Confluence boost evaluation error: {e}")

        return boost, reasons
