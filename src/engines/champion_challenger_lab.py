"""
Champion vs. Challenger A/B Shadow Lab Framework
================================================
Implements continuous, automated A/B strategy variant testing:
- Champions trade live on TradeLocker with real capital ($75 fixed risk).
- Challengers trade in the Shadow Lab ($0.00 live risk) testing looser/tighter gates.
- When a Challenger statistically beats a Champion over 30+ trades, it qualifies for promotion.
"""

import os
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional, Tuple

from src.core.config import Config
from src.core.database import get_db_connection

logger = logging.getLogger("ChampionChallengerLab")


@dataclass
class StrategyVariant:
    variant_id: str
    strategy_id: str
    variant_type: str  # 'CHAMPION' (Live) or 'CHALLENGER' (Shadow)
    parameters: Dict[str, Any]
    samples: int = 0
    wins: int = 0
    losses: int = 0
    total_r: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    is_active: bool = True
    created_at: str = ""


class ChampionChallengerLab:
    """
    Manages simultaneous live champion execution and shadow challenger benchmarking.
    """

    def __init__(self):
        self.init_db_tables()
        self.variants = self._load_or_create_variants()

    def init_db_tables(self):
        """Initializes the tournament tracking tables in SQLite."""
        try:
            conn = get_db_connection()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_tournament_variants (
                    variant_id TEXT PRIMARY KEY,
                    strategy_id TEXT,
                    variant_type TEXT,
                    parameters TEXT,
                    samples INTEGER,
                    wins INTEGER,
                    losses INTEGER,
                    total_r REAL,
                    profit_factor REAL,
                    win_rate REAL,
                    is_active INTEGER,
                    created_at TEXT
                );
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Tournament DB table init warning: {e}")

    def _load_or_create_variants(self) -> Dict[str, StrategyVariant]:
        """Loads existing tournament variants or registers defaults."""
        defaults = {
            # Strategy 9: Judas Inducement Hunter (GRADUATED CHAMPION)
            "STRAT_9_CHAMPION": StrategyVariant(
                variant_id="STRAT_9_CHAMPION",
                strategy_id="STRATEGY_9_JUDAS_INDUCEMENT",
                variant_type="CHAMPION",
                parameters={
                    "min_wick_pct": 60.0,
                    "min_atr_mult": 1.5,
                    "min_vol_mult": 1.5,
                    "stop_buffer_atr": 0.35,
                    "target_rr": 2.5
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            "STRAT_9_CHALLENGER": StrategyVariant(
                variant_id="STRAT_9_CHALLENGER",
                strategy_id="STRATEGY_9_JUDAS_INDUCEMENT",
                variant_type="CHALLENGER",
                parameters={
                    "min_wick_pct": 60.0,
                    "min_atr_mult": 1.5,
                    "min_vol_mult": 1.5,
                    "stop_buffer_atr": 0.35,
                    "target_rr": 2.5
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            # Strategy 1: Asian Session Range Fade
            "STRAT_1_CHAMPION": StrategyVariant(
                variant_id="STRAT_1_CHAMPION",
                strategy_id="STRATEGY_1_ASIAN_FADE",
                variant_type="CHAMPION",
                parameters={
                    "sweep_min_atr": 0.25,
                    "sweep_max_atr": 0.50,
                    "max_hurst": 0.45,
                    "target_rr": 2.5
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            "STRAT_1_CHALLENGER": StrategyVariant(
                variant_id="STRAT_1_CHALLENGER",
                strategy_id="STRATEGY_1_ASIAN_FADE",
                variant_type="CHALLENGER",
                parameters={
                    "sweep_min_atr": 0.20,
                    "sweep_max_atr": 0.80,
                    "min_wick_pct": 60.0,
                    "max_hurst": 0.48,
                    "target_rr": 3.0
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            # Strategy 8: Turtle Soup Liquidity Sweep
            "STRAT_8_CHAMPION": StrategyVariant(
                variant_id="STRAT_8_CHAMPION",
                strategy_id="STRATEGY_8_TURTLE_SOUP_SWEEP",
                variant_type="CHAMPION",
                parameters={
                    "min_smt_strength": 0.35,
                    "min_wick_pct": 50.0,
                    "target_rr": 3.0
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            "STRAT_8_CHALLENGER": StrategyVariant(
                variant_id="STRAT_8_CHALLENGER",
                strategy_id="STRATEGY_8_TURTLE_SOUP_SWEEP",
                variant_type="CHALLENGER",
                parameters={
                    "min_smt_strength": 0.20,
                    "allow_cvd_absorption": True,
                    "min_cvd_strength": 0.65,
                    "min_wick_pct": 45.0,
                    "target_rr": 3.0
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            # Strategy 3: Strong SMT Divergence NY AM Sweep
            "STRAT_3_CHALLENGER": StrategyVariant(
                variant_id="STRAT_3_SMT_DIVERGENCE_CHALLENGER",
                strategy_id="STRATEGY_3_SMT_DIVERGENCE",
                variant_type="CHALLENGER",
                parameters={
                    "session": "NY_AM",
                    "min_smt_strength": 0.70,
                    "min_wick_pct": 50.0,
                    "target_rr": 3.0,
                    "discount_premium_filter": True
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            # Strategy 5: XAU/USD Gold 50% Consequent Encroachment (CE) Longs (GRADUATED CHAMPION)
            "STRAT_5_XAU_GOLD_50PCT_CE_LONG": StrategyVariant(
                variant_id="STRAT_5_XAU_GOLD_50PCT_CE_LONG",
                strategy_id="STRATEGY_5_50PCT_CE_MT",
                variant_type="CHAMPION",
                parameters={
                    "symbol": "XAU/USD",
                    "direction": "LONG",
                    "min_htf_trend": "BULLISH",
                    "sessions": ["LONDON", "NY_AM", "ASIAN"],
                    "min_ce_mitigation_depth": 0.50,
                    "target_rr": 2.5,
                    "stop_buffer_atr": 0.25,
                    "weekdays_only": True
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            # Strategy 5: 50% Consequent Encroachment (CE) FVG Midpoint Fill (Shadow Challenger)
            "STRAT_5_CHALLENGER": StrategyVariant(
                variant_id="STRAT_5_50PCT_CE_MT_CHALLENGER",
                strategy_id="STRATEGY_5_50PCT_CE_MT",
                variant_type="CHALLENGER",
                parameters={
                    "timeframe": "1h",
                    "fvg_midpoint_fill_pct": 0.50,
                    "killzone_gated": True,
                    "target_rr": 3.5
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            # ──────────────────────────────────────────────────────────────────
            # VISUAL VECTOR CHALLENGERS (A/B Test vs. Live Champions)
            # ──────────────────────────────────────────────────────────────────
            "STRAT_9_VISUAL_VECTOR_CHALLENGER": StrategyVariant(
                variant_id="STRAT_9_VISUAL_VECTOR_CHALLENGER",
                strategy_id="STRATEGY_9_JUDAS_INDUCEMENT",
                variant_type="CHALLENGER",
                parameters={
                    "visual_vector_gating": True,
                    "min_analog_similarity": 0.85,
                    "block_on_trap_precedent": True,
                    "min_wick_pct": 70.0,
                    "target_rr": 3.0
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            "STRAT_8_VISUAL_VECTOR_CHALLENGER": StrategyVariant(
                variant_id="STRAT_8_VISUAL_VECTOR_CHALLENGER",
                strategy_id="STRATEGY_8_TURTLE_SOUP_SWEEP",
                variant_type="CHALLENGER",
                parameters={
                    "visual_vector_gating": True,
                    "min_analog_similarity": 0.85,
                    "block_on_trap_precedent": True,
                    "min_smt_strength": 0.35,
                    "target_rr": 3.0
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            "STRAT_3_VISUAL_VECTOR_CHALLENGER": StrategyVariant(
                variant_id="STRAT_3_VISUAL_VECTOR_CHALLENGER",
                strategy_id="STRATEGY_3_SMT_DIVERGENCE",
                variant_type="CHALLENGER",
                parameters={
                    "visual_vector_gating": True,
                    "min_analog_similarity": 0.85,
                    "session": "NY_AM",
                    "min_smt_strength": 0.70,
                    "target_rr": 3.0
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            # ──────────────────────────────────────────────────────────────────
            # CHRONOS TIME-SERIES CHALLENGERS (Shadow Lab 12-Candle Forecasts)
            # ──────────────────────────────────────────────────────────────────
            "STRAT_9_CHRONOS_SHADOW_CHALLENGER": StrategyVariant(
                variant_id="STRAT_9_CHRONOS_SHADOW_CHALLENGER",
                strategy_id="STRATEGY_9_JUDAS_INDUCEMENT",
                variant_type="CHALLENGER",
                parameters={
                    "chronos_time_series_gating": True,
                    "min_expansion_prob": 0.55,
                    "block_on_volatility_squeeze": False,
                    "target_rr": 3.0
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            "STRAT_8_CHRONOS_SHADOW_CHALLENGER": StrategyVariant(
                variant_id="STRAT_8_CHRONOS_SHADOW_CHALLENGER",
                strategy_id="STRATEGY_8_TURTLE_SOUP_SWEEP",
                variant_type="CHALLENGER",
                parameters={
                    "chronos_time_series_gating": True,
                    "min_expansion_prob": 0.55,
                    "target_rr": 3.0
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            "STRAT_3_CHRONOS_SHADOW_CHALLENGER": StrategyVariant(
                variant_id="STRAT_3_CHRONOS_SHADOW_CHALLENGER",
                strategy_id="STRATEGY_3_SMT_DIVERGENCE",
                variant_type="CHALLENGER",
                parameters={
                    "chronos_time_series_gating": True,
                    "min_expansion_prob": 0.55,
                    "session": "NY_AM",
                    "target_rr": 3.0
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            "SOL_SHADOW_CHALLENGER": StrategyVariant(
                variant_id="SOL_SHADOW_CHALLENGER",
                strategy_id="SOL_INDUCEMENT_HUNTER",
                variant_type="CHALLENGER",
                parameters={
                    "symbol": "SOL/USD",
                    "is_shadow_only": True,
                    "min_wick_pct": 55.0,
                    "target_rr": 3.0
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            "STRAT_9_PULLBACK_RELOAD_CHALLENGER": StrategyVariant(
                variant_id="STRAT_9_PULLBACK_RELOAD_CHALLENGER",
                strategy_id="STRATEGY_9_JUDAS_INDUCEMENT",
                variant_type="CHALLENGER",
                parameters={
                    "enable_pullback_reload": True,
                    "initial_scalp_take_profit_r": 1.2,
                    "reload_discount_pct": 0.50, # 50% retracement of initial push
                    "reload_stop_buffer_atr": 0.20,
                    "target_rr": 3.0
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            # ──────────────────────────────────────────────────────────────────
            # NON-ICT SMART MONEY CHALLENGERS (Shadow Lab $0 Live Capital Risk)
            # ──────────────────────────────────────────────────────────────────
            "STRAT_AMT_VALUE_REJECTION": StrategyVariant(
                variant_id="STRAT_AMT_VALUE_REJECTION",
                strategy_id="AUCTION_MARKET_THEORY_VALUE_REJECTION",
                variant_type="CHALLENGER",
                parameters={
                    "is_shadow_only": True,
                    "value_area_pct": 0.70,
                    "min_rejection_rr": 1.8,
                    "target_rr": 2.5
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            "STRAT_AVWAP_SIGMA_SNAPBACK": StrategyVariant(
                variant_id="STRAT_AVWAP_SIGMA_SNAPBACK",
                strategy_id="ANCHORED_VWAP_SIGMA_REVERSION",
                variant_type="CHALLENGER",
                parameters={
                    "is_shadow_only": True,
                    "anchor_period": "WEEKLY_SESSION",
                    "min_sigma_extension": 1.95,
                    "target_rr": 2.5
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            "STRAT_DELTA_ABSORPTION": StrategyVariant(
                variant_id="STRAT_DELTA_ABSORPTION",
                strategy_id="ORDERFLOW_DELTA_ICEBERG_ABSORPTION",
                variant_type="CHALLENGER",
                parameters={
                    "is_shadow_only": True,
                    "min_delta_pct": 0.40,
                    "min_wick_pct": 60.0,
                    "target_rr": 2.5
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            "STRAT_WYCKOFF_VSA_SPRING": StrategyVariant(
                variant_id="STRAT_WYCKOFF_VSA_SPRING",
                strategy_id="WYCKOFF_VSA_SPRING_UPTHRUST",
                variant_type="CHALLENGER",
                parameters={
                    "is_shadow_only": True,
                    "max_recovery_vol_mult": 1.4,
                    "target_rr": 2.5
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
            # ──────────────────────────────────────────────────────────────────
            # LOCAL MLX-M4 LORA CHALLENGER (A/B Test vs. Cloud Validators)
            # ──────────────────────────────────────────────────────────────────
            "CHALLENGER_LOCAL_MLX": StrategyVariant(
                variant_id="CHALLENGER_LOCAL_MLX",
                strategy_id="LOCAL_MLX_BAYESIAN_PIVOT_LORA",
                variant_type="CHALLENGER",
                parameters={
                    "model": "Qwen2.5-Coder-1.5B-Instruct-4bit",
                    "adapter": "adapters/bayesian-pivot-lora",
                    "engine": "mlx-lm",
                    "min_score": 7.5,
                    "target_rr": 2.5,
                    "live_risk": 0.0
                },
                created_at=datetime.now(timezone.utc).isoformat()
            ),
        }

        # Save defaults to SQLite if not present
        conn = None
        try:
            conn = get_db_connection()
            for v in defaults.values():
                conn.execute("""
                    INSERT OR IGNORE INTO strategy_tournament_variants (
                        variant_id, strategy_id, variant_type, parameters, samples,
                        wins, losses, total_r, profit_factor, win_rate, is_active, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    v.variant_id, v.strategy_id, v.variant_type, json.dumps(v.parameters),
                    v.samples, v.wins, v.losses, v.total_r, v.profit_factor, v.win_rate,
                    1 if v.is_active else 0, v.created_at
                ))
            conn.commit()
        except Exception as e:
            logger.debug(f"Variant sync error: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        return defaults

    def record_tournament_outcome(self, variant_id: str, is_win: bool, r_mult: float):
        """Records the outcome of a trade for a specific champion or challenger variant."""
        if variant_id not in self.variants:
            return

        v = self.variants[variant_id]
        v.samples += 1
        if is_win:
            v.wins += 1
            v.total_r += r_mult
        else:
            v.losses += 1
            v.total_r += r_mult

        total_resolved = v.wins + v.losses
        if total_resolved > 0:
            v.win_rate = round((v.wins / total_resolved) * 100.0, 1)
            # Profit Factor = (Wins * Avg Win R) / (Losses * Avg Loss R)
            gross_win = v.wins * (r_mult if r_mult > 0 else 2.5)
            gross_loss = max(1, v.losses) * 1.0
            v.profit_factor = round(gross_win / gross_loss, 2)

        # Persist to SQLite
        conn = None
        try:
            conn = get_db_connection()
            conn.execute("""
                UPDATE strategy_tournament_variants
                SET samples = ?, wins = ?, losses = ?, total_r = ?,
                    profit_factor = ?, win_rate = ?
                WHERE variant_id = ?
            """, (v.samples, v.wins, v.losses, v.total_r, v.profit_factor, v.win_rate, variant_id))
            conn.commit()
            logger.info(f"🏆 [A/B TOURNAMENT] {variant_id} ({v.variant_type}): Samples={v.samples} | WinRate={v.win_rate}% | PF={v.profit_factor}")
        except Exception as e:
            logger.debug(f"Tournament outcome record error: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        # Check if challenger qualifies for promotion
        if v.variant_type == "CHALLENGER":
            self._check_for_promotion(v)

    def _check_for_promotion(self, challenger: StrategyVariant):
        """Checks if a challenger variant has statistically beaten the live champion."""
        champion_id = challenger.variant_id.replace("_CHALLENGER", "_CHAMPION")
        champion = self.variants.get(champion_id)

        if not champion:
            return

        # Rapid Statistical Sample Threshold (8 resolved shadow trades)
        if challenger.samples >= 8:
            if challenger.profit_factor > champion.profit_factor and challenger.win_rate >= champion.win_rate:
                logger.info(
                    f"🚨 [RAPID PROMOTION QUALIFIED] {challenger.variant_id} (PF: {challenger.profit_factor}, WR: {challenger.win_rate}%, Samples: {challenger.samples}) "
                    f"outperformed {champion.variant_id} (PF: {champion.profit_factor}, WR: {champion.win_rate}%)!"
                )


    def get_tournament_leaderboard(self) -> str:
        """Returns an executive leaderboard markdown table comparing Champions vs Challengers."""
        lines = [
            "🏆 **A/B STRATEGY TOURNAMENT LEADERBOARD (CHAMPION vs CHALLENGER)**\n",
            "| Variant | Strategy | Type | Samples | Win Rate | Profit Factor | Total R |",
            "| :--- | :--- | :---: | :---: | :---: | :---: | :---: |"
        ]
        for v in self.variants.values():
            type_badge = "🟢 LIVE" if v.variant_type == "CHAMPION" else "👻 SHADOW"
            lines.append(
                f"| `{v.variant_id}` | {v.strategy_id.replace('STRATEGY_', '')} | {type_badge} | "
                f"{v.samples} | **{v.win_rate:.1f}%** | **{v.profit_factor:.2f}** | `{v.total_r:+.1f}R` |"
            )
        return "\n".join(lines)

    def evaluate_visual_challenger_gate(self, variant_id: str, symbol: str, direction: str, df: Any = None, setup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Evaluates whether a Visual Vector Challenger allows or blocks a trade based on visual precedent.
        """
        try:
            from src.engines.visual_vector_engine import VisualVectorEngine
            engine = VisualVectorEngine()
            query_vec = engine.extract_geometric_features(df, setup=setup)
            active_regime = (setup.get('regime') or setup.get('regime_type') or 'UNKNOWN') if setup else 'UNKNOWN'
            return engine.evaluate_visual_precedent(query_vec, symbol=symbol, direction=direction, regime_type=active_regime)
        except Exception as e:
            logger.debug(f"Visual challenger evaluation error: {e}")
            return {'recommendation': 'NEUTRAL', 'win_rate': 50.0, 'key_reason': str(e)}

    def evaluate_chronos_forecast_gate(self, variant_id: str, symbol: str, direction: str, df: Any = None) -> Dict[str, Any]:
        """
        Evaluates whether a Chronos Time-Series Shadow Challenger allows or blocks a trade based on 12-candle probability forecast.
        """
        try:
            from src.engines.chronos_engine import ChronosEngine
            c_engine = ChronosEngine()
            forecast = c_engine.forecast_12_candles(df, symbol=symbol)
            prob_up = forecast.get('prob_expansion_up', 0.33)
            prob_down = forecast.get('prob_expansion_down', 0.33)
            is_long = str(direction).upper() in ['BUY', 'LONG']
            
            dir_prob = prob_up if is_long else prob_down
            if dir_prob >= 0.55:
                recommendation = 'PASS_CONFIRMED'
            elif (prob_down >= 0.55 and is_long) or (prob_up >= 0.55 and not is_long):
                recommendation = 'REJECT_COUNTER_FORECAST'
            else:
                recommendation = 'NEUTRAL'

            return {
                'recommendation': recommendation,
                'forecast': forecast,
                'directional_probability': dir_prob,
                'key_reason': forecast.get('reasoning', '')
            }
        except Exception as e:
            logger.debug(f"Chronos challenger evaluation error: {e}")
            return {'recommendation': 'NEUTRAL', 'key_reason': str(e)}


