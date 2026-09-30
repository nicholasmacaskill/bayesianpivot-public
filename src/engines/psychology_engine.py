import os
import json
import logging
from datetime import datetime
from src.engines.ai_hub import SovereignAIHub
from src.core.config import Config

logger = logging.getLogger("PsychologyEngine")

class PsychologyEngine:
    def __init__(self, api_key=None):
        self.hub = SovereignAIHub()
            
        self.ledger_path = os.path.join(os.path.dirname(__file__), "psych_ledger.json")
        self.ledger = self._load_ledger()

    def _load_ledger(self):
        if os.path.exists(self.ledger_path):
            try:
                with open(self.ledger_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load psych ledger: {e}")
        return {"sessions": []}

    def _save_ledger(self):
        try:
            with open(self.ledger_path, 'w') as f:
                json.dump(self.ledger, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save psych ledger: {e}")

    def analyze_user_state(self, current_text=None, audio_path=None, physio_tilt=None):
        """
        Analyzes multimodal input to determine tilt level and sentiment.
        Optionally incorporates physio_tilt from BiometricEngine.
        """
        if not self.hub.has_ai:
            return {"tilt_score": 0, "sentiment": "Neutral", "reasoning": "AI Unavailable"}

        # Load Sovereign Prompts (Private IP)
        try:
            from src.sovereign_core.prompts.psychology_prompts import SOVEREIGN_PSYCHOLOGY_PROMPT
            sovereign_prompt = SOVEREIGN_PSYCHOLOGY_PROMPT
        except ImportError:
            sovereign_prompt = None

        if sovereign_prompt:
            prompt = sovereign_prompt.format(text=current_text or "No text provided")
        else:
            # Public Lite Version
            prompt = f"""
            Analyze the following trader text for emotional state.
            Trader text: "{current_text or 'No text provided'}"

            Return EXACTLY a JSON format like this, nothing else:
            {{
                "tilt_score": <1-10 score, where 1 is calm and 10 is highly tilted/emotional>,
                "sentiment": "<Calm, Frustrated, Anxious, Excited, or Neutral>",
                "reasoning": "<Reason for this classification>"
            }}
            """

        try:
            result = self.hub.analyze_setup(prompt)
            
            # Incorporate Physiological Tilt if provided
            if physio_tilt is not None:
                # If physio detects higher stress than AI, use physio
                result['tilt_score'] = max(result.get('tilt_score', 0), int(physio_tilt))
                result['physio_active'] = True
            
            # Update ledger
            session = {
                "timestamp": datetime.now().isoformat(),
                "tilt_score": result.get('tilt_score', 1),
                "sentiment": result.get('sentiment', 'Calm'),
                "text": current_text,
                "physio_tilt": physio_tilt
            }
            self.ledger['sessions'].append(session)
            if len(self.ledger['sessions']) > 50:
                self.ledger['sessions'].pop(0)
            self._save_ledger()
            
            return result
        except Exception as e:
            logger.error(f"Psychology analysis failed: {e}")
            if self.ledger.get('sessions'):
                last_session = self.ledger['sessions'][-1]
                return {
                    "tilt_score": last_session.get("tilt_score", 1),
                    "sentiment": last_session.get("sentiment", "Calm"),
                    "reasoning": f"AI API Error ({e}) - Fell back to last known state"
                }
            return {"tilt_score": 1, "sentiment": "Calm", "reasoning": str(e)}

    def get_risk_multiplier(self, tilt_score):
        """
        Returns a risk multiplier based on tilt level.
        Higher tilt = lower risk allowed (never cuts trading completely).
        """
        if tilt_score >= 8:
            return 0.25 # RISK FLOOR: Lower size instead of hard shutdown
        if tilt_score >= 6:
            return 0.5  # Half size
        if tilt_score >= 4:
            return 0.75 # 75% size
        return 1.0     # Normal

    def analyze_trapped_trader_perspective(
        self,
        symbol: str,
        trapped_side: str, # "LONGS" or "SHORTS"
        entry_price: float,
        current_price: float,
        underwater_minutes: int,
        underwater_bars: int,
        distance_atr: float,
        failed_relief_attempts: int = 1,
        invalidation_shelf: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Epistemic Perspective-Taking (Theory of Mind):
        Embody the collective psychology of the trapped retail cohort and determine
        how close they are to breaking (involuntary capitulation).
        """
        if not self.hub.has_ai:
            # Deterministic fallback if AI is offline
            proximity = min(0.20 + (underwater_minutes / 45.0) * 0.50 + (distance_atr / 2.0) * 0.30, 0.95)
            phase = "Exhaustion" if proximity >= 0.75 else ("Bargaining" if proximity >= 0.50 else "Denial")
            return {
                "breaking_point_proximity": round(proximity, 2),
                "psychological_phase": phase,
                "internal_monologue": f"Holding {trapped_side.lower()} in {phase.lower()}. Hope is deteriorating as price stays adverse.",
                "catalyst_to_break": f"Breach of invalidation level {invalidation_shelf or 'next key low'}.",
                "capitulation_velocity": "Accelerating" if proximity >= 0.75 else "Low",
                "reasoning": f"Calculated via deterministic stamina decay ({underwater_minutes}m, {distance_atr:.1f} ATR)."
            }

        prompt = f"""
You are a quantitative neuro-psychologist and market microstructure specialist.
Your task is Epistemic Perspective-Taking (Theory of Mind): You must embody the collective psychology of the retail crowd currently trapped in a specific market position and determine HOW CLOSE THEY ARE TO BREAKING (capitulating).

SCENARIO:
- Asset: {symbol}
- Trapped Cohort: Retail is currently {trapped_side} (baited into breakout/breakdown).
- Herd Entry Price: ${entry_price:,.2f}
- Current Price: ${current_price:,.2f} (Position is {distance_atr:.2f} ATR adverse/underwater).
- Time Elapsed Underwater: {underwater_minutes} minutes ({underwater_bars} consecutive 5-minute candles held in loss).
- Relief Attempts: The herd attempted {failed_relief_attempts} relief bounce(s) back toward entry; all were rejected by institutional limit absorption.
- Invalidation / Stop Shelf: ${invalidation_shelf:,.2f if invalidation_shelf else 'Resting swing level'} (where herd has clustered stop-loss and liquidation thresholds).

Answer this exact question:
HOW CLOSE IS THIS SPECIFIC GROUP OF HUMANS TO BREAKING?

Return a strict JSON object with these exact keys:
{{
  "breaking_point_proximity": <float between 0.0 and 1.0, where 1.0 is active involuntary capitulation>,
  "psychological_phase": "<Denial | Bargaining | Exhaustion | Active Capitulation>",
  "internal_monologue": "<2-3 sentences of what the trader's inner voice is experiencing right now in first person>",
  "catalyst_to_break": "<What specific price or time event triggers the final panic sell/buy dumping>",
  "capitulation_velocity": "<Low | Accelerating | Cascade Imminent>",
  "reasoning": "<1-2 sentences explaining why their biological pain threshold is or is not reached>"
}}
"""
        try:
            res = self.hub.analyze_setup(prompt)
            # Ensure required keys exist
            if "breaking_point_proximity" in res:
                return res
            # Fallback if structure varies slightly
            return {
                "breaking_point_proximity": float(res.get("score", 7.5)) / 10.0,
                "psychological_phase": "Exhaustion" if float(res.get("score", 7.5)) >= 7.5 else "Bargaining",
                "internal_monologue": res.get("reasoning", "Trader under severe pressure."),
                "catalyst_to_break": f"Breach of shelf at ${invalidation_shelf:,.2f if invalidation_shelf else 'key level'}",
                "capitulation_velocity": "Accelerating",
                "reasoning": res.get("reasoning", "Evaluated via Sovereign AI Hub.")
            }
        except Exception as e:
            logger.error(f"Theory of Mind perspective analysis failed: {e}")
            proximity = min(0.20 + (underwater_minutes / 45.0) * 0.50 + (distance_atr / 2.0) * 0.30, 0.95)
            return {
                "breaking_point_proximity": round(proximity, 2),
                "psychological_phase": "Exhaustion" if proximity >= 0.75 else "Bargaining",
                "internal_monologue": f"Trapped {trapped_side.lower()} running out of cognitive stamina.",
                "catalyst_to_break": "Shelf breach.",
                "capitulation_velocity": "Accelerating" if proximity >= 0.75 else "Low",
                "reasoning": f"Fallback error handler: {e}"
            }

if __name__ == "__main__":
    # Quick test
    engine = PsychologyEngine()
    print("--- User Tilt Test ---")
    state = engine.analyze_user_state(current_text="I just lost three trades in a row and I need to make it back now. BTC is definitely going down.")
    print(json.dumps(state, indent=2))
    
    print("\n--- Trapped Trader Theory of Mind Test ---")
    tom = engine.analyze_trapped_trader_perspective(
        symbol="XAU/USD",
        trapped_side="LONGS",
        entry_price=4188.50,
        current_price=4178.10,
        underwater_minutes=35,
        underwater_bars=7,
        distance_atr=1.30,
        failed_relief_attempts=2,
        invalidation_shelf=4171.00
    )
    print(json.dumps(tom, indent=2))
