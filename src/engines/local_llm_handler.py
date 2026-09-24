import requests
import json
import logging
import re
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Bayesian Pivot, an elite institutional quantitative trading AI validator specialized in Inner Circle Trader (ICT) and Smart Money Concepts (SMC) order flow mechanics.
Analyze this candidate trade setup and return a structured JSON evaluation with exactly these fields:
{
  "score": <float 0.0-10.0 continuous probability>,
  "verdict": "<FLOW_GO|SHADOW_OBSERVATION|REJECTED>",
  "reasoning": "<1-2 sentences on specific institutional confluence or trap veto>",
  "risk_multiplier": <float 0.0 to 1.33>,
  "risk_level": "<LOW|MEDIUM|HIGH>"
}
Scoring Rubric:
- 8.5-10.0: Tier 1 Unicorn (Sweep of HTF POI + strong SMT divergence + verified CVD limit absorption)
- 7.5-8.4: A-Tier Production Alpha (Clean killzone timing + HTF trend alignment)
- 5.0-7.4: Sub-Threshold (Quarantined to $0 risk shadow observation)
- 0.0-4.9: Toxic Retail Trap / Invalidation (Veto / Reject immediately)"""


class LocalLLMHandler:
    """
    Local Apple Silicon MLX LoRA inference engine for Bayesian Pivot.
    Primary: Fine-tuned LoRA model served on port 8080 via mlx-lm.
    If MLX is unavailable, scoring is skipped cleanly — no Ollama fallback.
    Ollama was removed: pre-LoRA legacy model, demonstrated 22% WR vs 84% WR for MLX.
    """
    def __init__(
        self,
        model: str = "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit",
        mlx_url: str = "http://127.0.0.1:8080/v1",
        timeout: int = 15
    ):
        self.model = model
        self.mlx_url = mlx_url.rstrip("/")
        self._timeout = timeout
        self.active_backend: Optional[str] = None
        self.active_provider: str = "MLX-LoRA-Local-M4"

    def is_available(self) -> bool:
        """
        Checks if the MLX LoRA server is running on port 8080.
        Returns False cleanly if not — no Ollama fallback.
        """
        try:
            resp = requests.get(f"{self.mlx_url}/models", timeout=1.5)
            if resp.status_code == 200:
                self.active_backend = "mlx"
                self.active_provider = "MLX-LoRA-Local-M4"
                return True
        except Exception:
            pass

        self.active_backend = None
        return False

    def build_5pillar_prompt(
        self,
        setup: Dict[str, Any],
        market_context: Optional[Dict] = None,
        hurst: float = 0.5,
        session_info: Optional[Dict] = None
    ) -> str:
        """
        Constructs the institutional 5-Pillar prompt matching the training distribution:
        SESSION, PD ARRAY, VOLUME, SMT CONFLUENCE, HTF STRUCTURE, ORDERFLOW.
        """
        # 1. Archetype & Direction
        raw_pattern = str(setup.get("pattern", "")).upper()
        direction = str(setup.get("direction", setup.get("bias", "LONG"))).upper()
        symbol = str(setup.get("symbol", "BTC/USD"))

        if "TURTLE" in raw_pattern or "SWEEP" in raw_pattern or "JUDAS" in raw_pattern:
            archetype = "TURTLE_SOUP_FADER"
        elif "EXPANSION" in raw_pattern or "TREND" in raw_pattern:
            archetype = "TREND_EXPANSION"
        else:
            archetype = "CORE_ANCHOR"

        # 2. Session Context
        session_name = (session_info.get("name") if session_info else setup.get("session", "UNKNOWN")).upper()
        if "NY" in session_name or "NEW YORK" in session_name:
            session_desc = "New York AM Session (Institutional Expansion)"
        elif "LONDON" in session_name:
            session_desc = "London Open Killzone (Institutional Drive)"
        elif "ASIA" in session_name:
            session_desc = "Asian Session (Retail Liquidity Accumulation)"
        elif "MACRO" in session_name or "SILVER" in session_name:
            session_desc = "Institutional Macro Window (High Liquidity)"
        else:
            session_desc = f"{session_name} Session"

        # 3. PD Array Dealing Range
        pd_array = setup.get("pd_array")
        if not pd_array:
            discount_pct = setup.get("discount_pct", None)
            if discount_pct is not None:
                if direction == "LONG" and discount_pct >= 0.5:
                    pd_array = f"Discount Dealing Range ({discount_pct*100:.0f}% Discount POI)"
                elif direction == "SHORT" and discount_pct <= 0.5:
                    pd_array = f"Premium Dealing Range ({(1-discount_pct)*100:.0f}% Premium POI)"
                else:
                    pd_array = "Equilibrium Dealing Range"
            else:
                if direction == "LONG":
                    pd_array = "Discount Dealing Range (HTF Bullish FVG / Order Block)"
                else:
                    pd_array = "Premium Dealing Range (HTF Bearish FVG / Order Block)"

        # 4. Volume Expansion
        vol_mult = setup.get("relative_volume", setup.get("vol_mult", 1.0))
        if vol_mult >= 1.5:
            volume_desc = f"{vol_mult:.1f}x Relative Volume Expansion"
        elif vol_mult <= 0.5:
            volume_desc = f"{vol_mult:.1f}x Relative Volume Trickle (Weak Retail)"
        else:
            volume_desc = f"{vol_mult:.1f}x Relative Volume (Neutral)"

        # 5. SMT Confluence
        smt_desc = setup.get("smt_confluence")
        if not smt_desc:
            smt_strength = setup.get("smt_strength", 0.0)
            if smt_strength > 0.4:
                smt_desc = f"Confirmed Intermarket SMT Divergence (Strength: {smt_strength:.2f})"
            elif market_context and market_context.get("DXY", {}).get("trend"):
                dxy_trend = market_context.get("DXY", {}).get("trend")
                smt_desc = f"Macro Dollar Bias: DXY {dxy_trend}"
            else:
                smt_desc = "N/A"

        # 6. HTF Structure
        htf_desc = setup.get("htf_structure")
        if not htf_desc:
            trend = setup.get("trend", setup.get("bias", "NEUTRAL"))
            hurst_text = f"Hurst {hurst:.2f}"
            htf_desc = f"{trend} Trend Alignment ({hurst_text})"

        # 7. Orderflow Dynamics
        orderflow_desc = setup.get("orderflow")
        if not orderflow_desc:
            cvd_abs = setup.get("cvd_absorption", False)
            if cvd_abs:
                orderflow_desc = "Verified CVD limit absorption at liquidity pool"
            else:
                orderflow_desc = "Intra-candle friction at entry zone (neutral CVD)"

        prompt = (
            f"EVALUATE INSTITUTIONAL SETUP:\n"
            f"ARCHETYPE: [{archetype}] | PROVENANCE: [ERA_4_SHADOW_LAB]\n"
            f"SYMBOL: {symbol} | DIRECTION: {direction}\n"
            f"SESSION: {session_desc}\n"
            f"PD ARRAY: {pd_array}\n"
            f"VOLUME: {volume_desc}\n"
            f"SMT CONFLUENCE: {smt_desc}\n"
            f"HTF STRUCTURE: {htf_desc}\n"
            f"ORDERFLOW: {orderflow_desc}"
        )
        return prompt

    def score_setup(
        self,
        setup: Dict[str, Any],
        market_context: Optional[Dict] = None,
        hurst: float = 0.5,
        session_info: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Scores candidate trade setup using the local MLX LoRA model.
        If MLX is unavailable, returns a neutral skip response (score=0, verdict=SHADOW_OBSERVATION)
        so the trade proceeds through the base system without MLX influence.
        """
        if not self.is_available():
            return {
                "score": 0.0,
                "verdict": "SHADOW_OBSERVATION",
                "reasoning": "MLX LoRA server offline — skipping local scoring, no Ollama fallback.",
                "risk_multiplier": 0.0,
                "risk_level": "MEDIUM",
                "provider": "Offline"
            }

        prompt = self.build_5pillar_prompt(
            setup=setup,
            market_context=market_context,
            hurst=hurst,
            session_info=session_info
        )

        try:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT + "\nKeep reasoning under 30 words so the JSON object closes cleanly."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
                "max_tokens": 350
            }
            resp = requests.post(
                f"{self.mlx_url}/chat/completions",
                json=payload,
                timeout=self._timeout
            )
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["choices"][0]["message"]["content"]
            result = self._parse_score(raw_text)
            result["provider"] = "MLX-LoRA-Local-M4"
            result["backend"] = "mlx"
            return result

        except Exception as e:
            logger.warning(f"MLX LoRA inference error: {e}")

        # Safe fallback — MLX errored mid-inference, skip cleanly
        return {
            "score": 0.0,
            "verdict": "SHADOW_OBSERVATION",
            "reasoning": "MLX LoRA inference error — skipping boost, no Ollama fallback.",
            "risk_multiplier": 0.0,
            "risk_level": "MEDIUM",
            "provider": "MLX-LoRA-Local-M4"
        }

    def analyze(self, prompt: str, image_path: Optional[str] = None) -> str:
        """
        Generic text inference method for ai_hub fallback compatibility.
        MLX only — raises RuntimeError if unavailable (no Ollama fallback).
        """
        if not self.is_available():
            raise RuntimeError("MLX LoRA server is not available. No Ollama fallback.")

        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 350
            }
            resp = requests.post(
                f"{self.mlx_url}/chat/completions",
                json=payload,
                timeout=self._timeout
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"MLX LoRA analyze error: {e}")
            raise e

    def _parse_score(self, raw: str) -> Dict[str, Any]:
        """Parse structured JSON scoring response with multi-pattern fallback."""
        clean = raw.strip()
        if clean.startswith("```json"):
            clean = clean[7:]
        elif clean.startswith("```"):
            clean = clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()

        # Strategy 1: Direct or Regex JSON load
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                score = float(data.get("score", 0.0))
                score = max(0.0, min(10.0, score))
                verdict = str(data.get("verdict", "SHADOW_OBSERVATION")).upper()
                if verdict not in ["FLOW_GO", "SHADOW_OBSERVATION", "REJECTED"]:
                    verdict = "SHADOW_OBSERVATION"
                reasoning = str(data.get("reasoning", "Local AI evaluation."))
                risk_mult = float(data.get("risk_multiplier", 1.0 if verdict == "FLOW_GO" else 0.0))
                if verdict != "FLOW_GO" or score < 7.5:
                    risk_mult = 0.0
                risk_mult = max(0.0, min(1.33, risk_mult))
                risk_lvl = str(data.get("risk_level", "MEDIUM")).upper()

                return {
                    "score": round(score, 2),
                    "verdict": verdict,
                    "reasoning": reasoning,
                    "risk_multiplier": round(risk_mult, 2),
                    "risk_level": risk_lvl
                }
            except Exception:
                pass

        # Strategy 2: Resilient field extraction if JSON closing brace was truncated
        try:
            score_m = re.search(r'"score"\s*:\s*([0-9.]+)', clean)
            verdict_m = re.search(r'"verdict"\s*:\s*"([^"]+)"', clean)
            reasoning_m = re.search(r'"reasoning"\s*:\s*"([^"]+)"', clean)
            risk_m = re.search(r'"risk_multiplier"\s*:\s*([0-9.]+)', clean)
            level_m = re.search(r'"risk_level"\s*:\s*"([^"]+)"', clean)

            if score_m or verdict_m:
                score = float(score_m.group(1)) if score_m else 5.0
                score = max(0.0, min(10.0, score))
                verdict = verdict_m.group(1).upper() if verdict_m else ("FLOW_GO" if score >= 7.5 else "REJECTED")
                reasoning = reasoning_m.group(1) if reasoning_m else clean[:120].replace("\n", " ")
                risk_mult = float(risk_m.group(1)) if risk_m else (1.0 if verdict == "FLOW_GO" else 0.0)
                if verdict != "FLOW_GO" or score < 7.5:
                    risk_mult = 0.0
                risk_mult = max(0.0, min(1.33, risk_mult))
                risk_lvl = level_m.group(1).upper() if level_m else "MEDIUM"

                return {
                    "score": round(score, 2),
                    "verdict": verdict,
                    "reasoning": reasoning,
                    "risk_multiplier": round(risk_mult, 2),
                    "risk_level": risk_lvl
                }
        except Exception as e:
            logger.warning(f"Resilient parse error: {e}")

        return {
            "score": 0.0,
            "verdict": "SHADOW_OBSERVATION",
            "reasoning": "Output parsing fallback.",
            "risk_multiplier": 0.0,
            "risk_level": "MEDIUM"
        }
