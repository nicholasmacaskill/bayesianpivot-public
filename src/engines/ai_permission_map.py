import os
import json
import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

MAP_FILE_PATH = "/tmp/sovereign_ai_permission_map.json"

class AIPermissionMap:
    """
    Asynchronous In-Memory AI Permission & RAG Knowledge Map.
    
    Provides sub-millisecond (<0.5ms) lookup of macro AI bias, RAG historical 
    similarity, and permitted archetypes for fast-lane deterministic execution engines.
    """
    _cache: Dict[str, Any] = {}
    _last_read_ts: float = 0.0

    @classmethod
    def set_permission(
        cls, 
        symbol: str, 
        ai_bias: str, 
        conviction_score: float, 
        regime: str, 
        rag_similarity: float = 0.0,
        authorized_archetypes: list = None,
        notes: str = ""
    ) -> None:
        """
        Called by the 1H/4H AI Validator / RAG Engine to publish pre-computed macro intelligence.
        """
        data = cls._read_file()
        data[symbol] = {
            "symbol": symbol,
            "timestamp": time.time(),
            "ai_bias": ai_bias, # "BULLISH", "BEARISH", "NEUTRAL"
            "conviction_score": round(float(conviction_score), 2),
            "regime": regime,
            "rag_similarity": round(float(rag_similarity), 2),
            "authorized_archetypes": authorized_archetypes or [
                "JUDAS_INDUCEMENT_SNIPER",
                "LONDON_CLOSE_SILVER_BULLET",
                "TURTLE_SOUP_LIQUIDITY_SWEEP",
                "STRAT_5_XAU_GOLD_50PCT_CE_LONG",
                "FVG_50PCT_CE_REVERSAL_LONG",
                "FVG_50PCT_CE_REVERSAL",
                "FVG_CONSEQUENT_ENCROACHMENT",
                "FVG_50PCT_CE_REVERSAL_SHADOW"
            ],
            "notes": notes
        }
        cls._write_file(data)
        cls._cache = data
        cls._last_read_ts = time.time()
        logger.info(f"🧠 [AI Permission Map] Updated {symbol}: Bias={ai_bias} | Conviction={conviction_score}/10 | RAG={rag_similarity}%")

    @classmethod
    def get_permission(cls, symbol: str) -> Dict[str, Any]:
        """
        Ultra-fast (<0.5ms) zero-latency lookup for real-time 5m execution engines.
        """
        now = time.time()
        if now - cls._last_read_ts > 10.0 or not cls._cache:
            cls._cache = cls._read_file()
            cls._last_read_ts = now

        entry = cls._cache.get(symbol)
        if not entry:
            # Default safe baseline if no 1H AI update exists yet
            return {
                "symbol": symbol,
                "timestamp": now,
                "ai_bias": "NEUTRAL",
                "conviction_score": 7.5,
                "regime": "MEAN_REVERSION",
                "rag_similarity": 50.0,
                "authorized_archetypes": [
                    "JUDAS_INDUCEMENT_SNIPER",
                    "LONDON_CLOSE_SILVER_BULLET",
                    "TURTLE_SOUP_LIQUIDITY_SWEEP",
                    "STRAT_5_XAU_GOLD_50PCT_CE_LONG",
                    "FVG_50PCT_CE_REVERSAL_LONG",
                    "FVG_50PCT_CE_REVERSAL",
                    "FVG_CONSEQUENT_ENCROACHMENT",
                    "FVG_50PCT_CE_REVERSAL_SHADOW",
                    "AVWAP_2SIGMA_BEARISH_SNAPBACK",
                    "AVWAP_2SIGMA_BULLISH_SNAPBACK",
                    "WYCKOFF_VSA_SPRING",
                    "WYCKOFF_VSA_UPTHRUST",
                    "AMT_VALUE_AREA_HIGH_REJECTION",
                    "AMT_VALUE_AREA_LOW_REJECTION"
                ],
                "notes": "Default baseline permission"
            }
            
        # Check if entry is older than 4 hours (stale safeguard)
        if now - entry.get("timestamp", 0) > 14400:
            entry["is_stale"] = True
        else:
            entry["is_stale"] = False
            
        return entry

    @classmethod
    def evaluate_confluence(cls, symbol: str, direction: str, pattern_type: str) -> tuple[bool, float, str]:
        """
        Evaluates real-time 5m setup against pre-computed 1H AI RAG state.
        Returns: (is_approved, dynamic_risk_multiplier, reason_msg)
        """
        perm = cls.get_permission(symbol)
        bias = perm.get("ai_bias", "NEUTRAL")
        conviction = perm.get("conviction_score", 7.5)
        rag_sim = perm.get("rag_similarity", 50.0)
        auth_archetypes = perm.get("authorized_archetypes", [])

        if perm.get("is_stale"):
            return False, 0.0, "AI Permission is STALE (older than 4 hours). Execution blocked."

        # 1. Archetype Authorization (exact or normalized alias match)
        pattern_clean = pattern_type.upper().replace("_SHADOW", "")
        auth_clean = [a.upper().replace("_SHADOW", "") for a in auth_archetypes]
        is_auth = (pattern_type in auth_archetypes) or (pattern_clean in auth_clean) or any(
            ("FVG" in pattern_clean and ("FVG" in a or "50PCT" in a or "STRAT_5" in a)) or
            ("TURTLE_SOUP" in pattern_clean and "TURTLE_SOUP" in a) or
            ("JUDAS" in pattern_clean and "JUDAS" in a) or
            ("AVWAP" in pattern_clean and "AVWAP" in a) or
            ("WYCKOFF" in pattern_clean and "WYCKOFF" in a) or
            ("AMT" in pattern_clean and "AMT" in a)
            for a in auth_clean
        )
        if not is_auth:
            return False, 0.0, f"Archetype {pattern_type} not in authorized list {auth_archetypes}"

        # 2. Bias Alignment Check
        is_aligned = (
            (bias == "BULLISH" and direction in ("BUY", "LONG")) or
            (bias == "BEARISH" and direction in ("SELL", "SHORT")) or
            bias == "NEUTRAL"
        )
        
        is_counter_trend = (
            (bias == "BULLISH" and direction in ("SELL", "SHORT")) or
            (bias == "BEARISH" and direction in ("BUY", "LONG"))
        )

        # Mean-reversion sweep archetypes are designed to fade manipulation extremes
        is_mean_reversion_sweep = any(
            k in pattern_clean for k in [
                "TURTLE_SOUP", "JUDAS", "SILVER_BULLET", "FVG", "50PCT", "STRAT_5", 
                "SWEEP", "REVERSAL", "AVWAP", "WYCKOFF", "AMT"
            ]
        )

        # Enforce Hard Counter-Trend Ban ONLY on generic breakout / trend-continuation setups
        if is_counter_trend and not is_mean_reversion_sweep:
            return False, 0.0, f"Hard Counter-Trend Block: {direction} prohibited during active 1H/4H {bias} bias (Conviction {conviction}/10)"

        # 3. Dynamic Sizing Multiplier (Asymmetric Risk)
        if is_counter_trend and is_mean_reversion_sweep:
            risk_mult = 0.5  # Standard probe allocation for fading manipulation extremes
            msg = f"Counter-Trend Liquidity Sweep Authorized (Fading {bias} manipulation extreme, Conviction: {conviction}/10)"
        elif is_aligned and conviction >= 8.5 and rag_sim >= 70.0:
            risk_mult = 1.0  # High-conviction full allocation
            msg = f"Full High-Alpha Confluence (AI: {conviction}/10, RAG: {rag_sim}%, Bias: {bias})"
        else:
            risk_mult = 0.5  # Standard probe size (aligned with trend or neutral)
            msg = f"Standard Confluence (AI: {conviction}/10, Bias: {bias})"

        return True, risk_mult, msg

    @classmethod
    def _read_file(cls) -> Dict[str, Any]:
        if not os.path.exists(MAP_FILE_PATH):
            return {}
        try:
            with open(MAP_FILE_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    @classmethod
    def _write_file(cls, data: Dict[str, Any]) -> None:
        try:
            with open(MAP_FILE_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to persist AI permission map: {e}")
