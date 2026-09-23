import os
import json
import logging
import numpy as np
import pandas as pd
from src.core.config import Config
from src.engines.ai_hub import SovereignAIHub

logger = logging.getLogger(__name__)

class AIValidator:
    """
    AI Validator — Bayesian Pivot Infra
    ======================================
    """
    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.hub = SovereignAIHub()
            
        # Load ICT Oracle Knowledge Base
        self.kb_path = os.path.join(os.getcwd(), "docs", "ict_oracle_kb.json")
        if not os.path.exists(self.kb_path):
            self.kb_path = os.path.join(os.getcwd(), "src", "sovereign_core", "ict_oracle_kb.json")
        if not os.path.exists(self.kb_path):
            self.kb_path = os.path.join(os.path.dirname(__file__), "ict_oracle_kb.json")
            
        self.oracle_kb = {}
        if os.path.exists(self.kb_path):
            try:
                with open(self.kb_path, 'r') as f:
                    self.oracle_kb = json.load(f)
            except Exception as e:
                print(f"⚠️ Failed to load Oracle KB: {e}")

        # Load Sovereign Prompts (Private IP)
        try:
            from src.sovereign_core.prompts.validator_prompts import SOVEREIGN_VALIDATOR_PROMPT, SOVEREIGN_VISION_PROMPT
            self.sovereign_prompt = SOVEREIGN_VALIDATOR_PROMPT
            self.sovereign_vision = SOVEREIGN_VISION_PROMPT
            self.is_lite = False
        except ImportError:
            self.sovereign_prompt = None
            self.sovereign_vision = None
            self.is_lite = True

    def _get_oracle_prompt(self, pattern):
        """Extracts relevant ground truth from the Oracle KB."""
        if not self.oracle_kb:
            return ""
        
        concepts = self.oracle_kb.get('core_concepts', {})
        ground_truth = "### 🔮 THE ORACLE GROUND TRUTH (MICHAEL'S TEACHINGS):\n"
        
        # Match pattern to KB concept flexibly
        matched = False
        pat_lower = pattern.lower()
        for key, details in concepts.items():
            key_clean = key.lower().replace("_", " ")
            key_root = key_clean.split()[0]  # e.g., 'judas' from 'judas swing'
            if key_clean in pat_lower or key_root in pat_lower:
                ground_truth += f"- {details.get('full_name', key)}: {details.get('logic', details.get('definition', ''))}\n"
                if 'validation' in details:
                    ground_truth += f"  - Validation Rule: {details['validation']}\n"
                matched = True
        
        # Default fallback to core if no specific pattern matched
        if not matched:
            po3 = concepts.get('PO3', {})
            ground_truth += f"- PO3 Baseline: {po3.get('logic', 'Accumulation, Manipulation, Distribution.')}\n"
            
        return ground_truth
    
    def detect_market_regime(self, df):
        """
        Classifies current market regime based on volatility and trend characteristics.
        
        Returns:
            str: Regime classification (High-Volatility Expansion, Low-Volatility Consolidation, etc.)
        """
        if df is None or len(df) < 50:
            return "Unknown (Insufficient Data)"
        
        try:
            # Calculate ATR
            high = df['high']
            low = df['low']
            close = df['close']
            
            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(window=14).mean()
            
            current_atr = atr.iloc[-1]
            mean_atr = atr.iloc[-50:].mean()
            
            # Volatility classification
            if pd.isna(current_atr) or pd.isna(mean_atr):
                return "Unknown (ATR Calculation Failed)"
            
            vol_ratio = current_atr / mean_atr if mean_atr > 0 else 1.0
            
            # Trend detection (simple EMA cross)
            ema_20 = close.ewm(span=20).mean().iloc[-1]
            ema_50 = close.ewm(span=50).mean().iloc[-1]
            
            # Range analysis (last 20 candles)
            recent_high = df['high'].iloc[-20:].max()
            recent_low = df['low'].iloc[-20:].min()
            range_pct = (recent_high - recent_low) / recent_low if recent_low > 0 else 0
            
            # Classification logic
            if vol_ratio > 1.5:
                if ema_20 > ema_50 * 1.02 or ema_20 < ema_50 * 0.98:
                    return "High-Volatility Trending"
                else:
                    return "High-Volatility Expansion"
            elif vol_ratio < 1.0:
                if range_pct < 0.02:  # Less than 2% range
                    return "Low-Volatility Consolidation"
                else:
                    return "Low-Volatility Ranging"
            else:
                if abs(ema_20 - ema_50) / ema_50 < 0.01:
                    return "Normal-Volatility Choppy"
                else:
                    return "Normal-Volatility Trending"
                    
        except Exception as e:
            print(f"⚠️ Regime detection failed: {e}")
            return "Unknown (Error)"
    
    def calculate_dynamic_risk(self, score, regime, news_context, setup=None):
        """
        98% Reliability Refactor: Non-Linear Risk Penalty & Institutional Convergence.
        """
        base_risk = 0.0075  # 0.75%
        multiplier = 1.0
        reasoning = []
        
        # 1. 98% Standard: Staged Risk Scaling (Tiered by Trust/Score)
        # Trust Score logic is prioritized for the final risk cap
        trust_score = setup.get('guard_trust_score', 100) if setup else 100
        
        # Tiered Tiering (Task 5)
        if trust_score >= Config.get('AI_TRUST_TIER_AGGRESSIVE', 90) and score >= 9.0:
            multiplier = 1.33 # Scale 0.75% -> 1.0%
            reasoning.append(f"AGGRESSIVE SNIPER: High Trust ({trust_score}) + Elite Score ({score})")
        elif trust_score >= Config.get('AI_TRUST_TIER_CONSERVATIVE', 75):
            multiplier = 0.67 # Scale 0.75% -> 0.5%
            reasoning.append(f"CONSERVATIVE MODE: Trust {trust_score} (75-89 Tier)")
        else:
            multiplier = 0.0
            reasoning.append(f"MONITOR ONLY: Trust {trust_score} below minimum reliability threshold.")
            return {
                "multiplier": 0.0,
                "suggested_risk_pct": 0.0,
                "reasoning": " | ".join(reasoning)
            }

        # 1.5. Score-based adjustments within the permitted Tier
        if score >= 9.2:
            multiplier *= 1.1 
            multiplier = min(multiplier, 1.33)
        elif score < 7.5:
            multiplier *= 0.8

        # 2. Institutional Convergence (Macro + HTF OB + 5M MSS)
        # We look for a high SMT + Alignment in setup
        smt = setup.get('smt_strength', 0) if setup else 0
        has_mss = setup.get('mss_detected', False) if setup else False
        
        if smt >= 0.7 and has_mss:
            multiplier = max(multiplier, 1.33)
            reasoning.append("Institutional Convergence Detected (Strong SMT + MSS)")

        # 3. Non-Linear News Penalty
        news_upper = news_context.upper()
        if "HIGH IMPACT" in news_upper:
            multiplier *= 0.4
            reasoning.append("High Impact News penalty (-60%)")
        elif "MEDIUM IMPACT" in news_upper:
            multiplier *= 0.75
            reasoning.append("Medium Impact News penalty (-25%)")
        elif "LOW IMPACT" in news_upper:
            # Task: Allow full risk if Low Impact and Convergence detected
            if smt >= 0.7:
                reasoning.append("Low Impact News ignored due to Institutional Convergence.")
            else:
                multiplier *= 0.95
                reasoning.append("Low Impact News minor penalty (-5%)")

        return {
            "multiplier": round(multiplier, 2),
            "suggested_risk_pct": round(base_risk * multiplier * 100, 2),
            "reasoning": " | ".join(reasoning)
        }
    
    def estimate_slippage(self, symbol, entry_price, position_size, exchange=None):
        """
        Estimates slippage based on L2 order book depth.
        
        Args:
            symbol: Trading pair
            entry_price: Intended entry price
            position_size: Position size in base currency
            exchange: CCXT exchange instance (optional)
            
        Returns:
            dict: Contains slippage estimate and quality rating
        """
        if exchange is None:
            return {
                "slippage_pct": None,
                "quality": "Unknown",
                "reasoning": "Order book unavailable"
            }
        
        try:
            order_book = exchange.fetch_order_book(symbol, limit=50)
            
            # Determine direction based on position size sign
            if position_size > 0:  # Buying
                asks = order_book['asks']
                cumulative_volume = 0
                total_cost = 0
                
                for price, volume in asks:
                    if cumulative_volume >= position_size:
                        break
                    fill_volume = min(volume, position_size - cumulative_volume)
                    total_cost += price * fill_volume
                    cumulative_volume += fill_volume
                
                if cumulative_volume > 0:
                    avg_fill_price = total_cost / cumulative_volume
                    slippage_pct = ((avg_fill_price - entry_price) / entry_price) * 100
                else:
                    slippage_pct = None
                    
            else:  # Selling
                bids = order_book['bids']
                cumulative_volume = 0
                total_proceeds = 0
                abs_position = abs(position_size)
                
                for price, volume in bids:
                    if cumulative_volume >= abs_position:
                        break
                    fill_volume = min(volume, abs_position - cumulative_volume)
                    total_proceeds += price * fill_volume
                    cumulative_volume += fill_volume
                
                if cumulative_volume > 0:
                    avg_fill_price = total_proceeds / cumulative_volume
                    slippage_pct = ((entry_price - avg_fill_price) / entry_price) * 100
                else:
                    slippage_pct = None
            
            # Quality rating
            if slippage_pct is None:
                quality = "Unknown"
                reasoning = "Insufficient liquidity data"
            elif slippage_pct < 0.05:
                quality = "Excellent"
                reasoning = "Deep liquidity, minimal slippage expected"
            elif slippage_pct < 0.15:
                quality = "Acceptable"
                reasoning = "Moderate liquidity, acceptable slippage"
            else:
                quality = "Poor"
                reasoning = "Shallow liquidity, high slippage risk"
            
            return {
                "slippage_pct": round(slippage_pct, 3) if slippage_pct else None,
                "quality": quality,
                "reasoning": reasoning
            }
            
        except Exception as e:
            print(f"⚠️ Slippage estimation failed: {e}")
            return {
                "slippage_pct": None,
                "quality": "Unknown",
                "reasoning": f"Error: {str(e)}"
            }

    def hard_logic_audit(self, setup, df=None):
        """
        Air-Gapped Fallback: Mathematical validation when AI is unavailable.
        Now includes dual-track output.
        """
        score = 0
        reasoning_parts = []
        
        smt = setup.get('smt_strength', 0)
        if smt >= 0.5:
            score += 3
            reasoning_parts.append(f"Strong SMT ({smt})")
        
        cross_asset = setup.get('cross_asset_divergence', 0)
        if abs(cross_asset) >= 0.5:
            score += 3
            reasoning_parts.append(f"Cross-Asset Aligned ({cross_asset})")
        
        if setup.get('time_quartile', {}).get('num') == 2:
            score += 2
            reasoning_parts.append("Q2 Judas Window")
        
        if setup.get('is_discount') or setup.get('is_premium'):
            score += 2
            reasoning_parts.append("Valid Quartile")

        # Major Structural Liquidity Sweep & Heatmap Density Confluence Bonus
        swept_type = setup.get('swept_level_type', '')
        wick_pct = max(setup.get('lower_wick_pct', 0), setup.get('upper_wick_pct', 0), setup.get('wick_pct', 0))
        vol_mult = setup.get('vol_mult', 1.0)
        liq_density = setup.get('liq_density', 0.0)
        
        if liq_density >= 6.0:
            score += min(liq_density * 0.3, 3.0)
            reasoning_parts.append(f"Dense Stop Cluster (Score: {liq_density:.1f}/10)")
        elif (swept_type in ['EQUAL_LOWS', 'EQUAL_HIGHS', 'SESSION_LOW', 'SESSION_HIGH', 'PDL', 'PDH'] and wick_pct >= 50.0) or (wick_pct >= 60.0 and vol_mult >= 3.0):
            score += 2
            reasoning_parts.append(f"Major Structural Sweep ({swept_type or 'Wick Rejection'}, {wick_pct:.0f}% wick, {vol_mult:.1f}x vol)")
        
        # --- Sovereign Score Normalization (Global Mode) ---
        # If markets are closed (cross_asset == 0), normalize score to 10-base
        # Institutional Grade (>8.5) MUST be achievable via crypto-native SMT alone
        from datetime import datetime, timezone
        utc_hour = datetime.now(timezone.utc).hour
        is_us_equities = 13 <= utc_hour < 20
        
        if not is_us_equities and abs(cross_asset) < 0.1:
            # We are in Asian/London, Equity Markets closed.
            # Scale score from 7-max to 10-max (approx)
            score = (score / 7.0) * 10 if score > 0 else 0
            reasoning_parts.append("Session Normalized (US Closed)")
        
        # Detect regime for shadow track
        regime = self.detect_market_regime(df) if df is not None else "Unknown"
        news_context = setup.get('news_context', 'Clear')
        risk_calc = self.calculate_dynamic_risk(score, regime, news_context, setup=setup)
        
        return {
            "live_execution": {
                "score": float(score),
                "verdict": "HARD_LOGIC_PASS" if score >= 7 else "HARD_LOGIC_REJECT",
                "reasoning": f"FALLBACK MODE: {' | '.join(reasoning_parts)}. Score: {score}/10",
                "execution_logic": "Standard 1:3 RR with tight SL at invalidation point",
                "discipline_check": "Air-gapped audit - AI unavailable"
            },
            "shadow_optimizer": {
                "suggested_risk_multiplier": risk_calc['multiplier'],
                "regime_classification": regime,
                "alpha_delta_prediction": "N/A (Fallback mode)",
                "slippage_estimate": "N/A",
                "optimization_reasoning": risk_calc['reasoning']
            }
        }

    def check_directional_cooldown(self, symbol: str, direction: str, cooldown_hours: float = 4.0) -> tuple[bool, str]:
        """
        Checks if a trade in the SAME direction was stopped out within `cooldown_hours`.
        Returns (is_allowed, reason).
        """
        try:
            import sqlite3
            import os
            import pandas as pd
            from datetime import datetime, timedelta
            db_path = os.path.join(os.getcwd(), "data", "smc_alpha.db")
            if not os.path.exists(db_path):
                return True, "DB not found for cooldown check"
                
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            
            rows = c.execute(
                "SELECT timestamp, symbol, side, pnl FROM journal WHERE status = 'CLOSED' AND pnl < 0 ORDER BY rowid DESC LIMIT 30"
            ).fetchall()
            conn.close()
            
            if not rows:
                return True, "No recent losing trades found"
                
            cutoff = datetime.utcnow() - timedelta(hours=cooldown_hours)
            target_side = str(direction or "BUY").upper()
            target_sym = str(symbol or "").replace("/", "").replace("_", "").upper()
            
            for r in rows:
                ts_str, r_sym, side, pnl = r[0], str(r[1] or ""), str(r[2]).upper(), float(r[3] or 0.0)
                clean_r_sym = r_sym.replace("/", "").replace("_", "").upper()
                if target_sym and clean_r_sym and target_sym != clean_r_sym:
                    continue
                try:
                    ts = pd.to_datetime(ts_str).tz_localize(None)
                except Exception:
                    continue
                    
                if ts >= cutoff and side == target_side:
                    hrs_ago = (datetime.utcnow() - ts).total_seconds() / 3600.0
                    return False, f"DIRECTIONAL COOLDOWN: Stopped out on {symbol} {side} trade {hrs_ago:.1f}h ago (< {cooldown_hours}h limit). Cooldown active."
                    
            return True, "Directional cooldown clear"
        except Exception as e:
            return True, f"Cooldown check bypass on error: {e}"

    def analyze_trade(self, setup, sentiment, whales, image_path=None, df=None, exchange=None, memory_context=None, hurst_exponent=None, guard_trust_score=None):
        """
        Calls Gemini API to validate the setup with DUAL-TRACK analysis.
        
        Args:
            setup: Trade setup dict
            sentiment: Market sentiment data
            whales: Whale activity data
            image_path: Optional chart image path
            df: Optional dataframe for regime detection
            exchange: Optional CCXT exchange for slippage estimation
            memory_context: Optional historical context from RAG
            hurst_exponent: Optional Hurst exponent for market regime
            guard_trust_score: Optional trust score from GuardEngine
        
        Returns:
            dict: Dual-track analysis with live_execution and shadow_optimizer sections
        """
        if guard_trust_score is not None:
            setup['guard_trust_score'] = guard_trust_score

        if not self.hub.has_ai:
            # Fallback to hard logic if AI unavailable
            return self.hard_logic_audit(setup, df)

        # Dynamic Oracle Grounding
        oracle_rules = self._get_oracle_prompt(setup.get('pattern', 'PO3'))
        
        # Detect market regime for shadow track
        regime = self.detect_market_regime(df) if df is not None else "Unknown"
        
        # Integrate Hurst status into regime description
        if hurst_exponent is not None:
            regime_detail = f"{regime} (Hurst: {hurst_exponent:.2f} - {'Trending' if hurst_exponent > 0.45 else 'Mean-Reverting' if hurst_exponent < 0.35 else 'Neutral'})"
        else:
            regime_detail = regime

        # Calculate slippage estimate
        entry_price = setup.get('entry', 0)
        position_size = setup.get('position_size_estimate', 1.0)  # Estimate for slippage calc
        slippage_info = self.estimate_slippage(
            setup['symbol'], 
            entry_price, 
            position_size, 
            exchange
        )

        # Detect Session for Normalization
        from datetime import datetime, timezone
        utc_hour = datetime.now(timezone.utc).hour
        is_us_equities = 13 <= utc_hour < 20
        normalization_hint = ""
        if not is_us_equities:
            normalization_hint = (
                "\n### GLOBAL LIQUIDITY MODE (NON-US HOURS):\n"
                "- US Equities (NQ/ES) are closed. Shift weighting HEAVILY to DXY and Treasury Yields.\n"
                "- Ensure Institutional Grade (>8.5) can be achieved via Crypto-Native SMT alone.\n"
                "- Do NOT penalize the setup for neutral NQ/ES data.\n"
            )

        # DUAL-TRACK PROMPT CONSTRUCTION
        if self.sovereign_prompt:
            # Full Sovereign Version (Master Theory active)
            memory_context = None
            try:
                from src.engines.shadow_chart_memory import ShadowChartMemory
                shadow_mem = ShadowChartMemory()
                cases = shadow_mem.retrieve_similar_cases(
                    pattern=setup.get('pattern', ''),
                    symbol=setup.get('symbol', 'BTC/USD'),
                    direction=setup.get('direction', 'LONG'),
                    limit=2,
                    df=df
                )
                memory_context = shadow_mem.format_memory_for_prompt(cases)
            except Exception as _mem_err:
                logger.debug(f"Shadow memory retrieval error: {_mem_err}")

            safe_memory = memory_context if memory_context else "No highly similar historical setups found for reference."
            
            entry_val = setup.get('entry') or setup.get('entry_price', 0.0)
            sl_val = setup.get('stop_loss', 0.0)
            tp_val = setup.get('target') or setup.get('take_profit', 0.0)
            risk_dist = abs(entry_val - sl_val) if entry_val and sl_val else 0.0
            risk_dist_str = f"${risk_dist:,.2f}" if risk_dist > 0 else "Dynamic"
            
            prompt = self.sovereign_prompt.format(
                symbol=setup['symbol'],
                pattern=setup.get('pattern', 'SMC Logic'),
                entry=f"${entry_val:,.2f}" if entry_val else "Market / Dynamic",
                stop_loss=f"${sl_val:,.2f}" if sl_val else "Dynamic Invalidation",
                target=f"${tp_val:,.2f}" if tp_val else "Dynamic Target",
                risk_dist=risk_dist_str,
                phase=setup.get('time_quartile', {}).get('phase', 'Unknown'),
                position='Deep Discount' if setup.get('is_discount') else 'Premium' if setup.get('is_premium') else 'Neutral',
                smt_strength=setup.get('smt_strength', 0),
                min_smt=Config.get('MIN_SMT_STRENGTH', 0.35),
                cross_asset=setup.get('cross_asset_divergence', 0),
                bias=setup.get('bias', 'Neutral'),
                news=setup.get('news_context', 'Clear'),
                sentiment=sentiment,
                whales=whales,
                regime=regime_detail,
                slippage_pct=slippage_info.get('slippage_pct', 'N/A'),
                slippage_quality=slippage_info.get('quality', 'Unknown'),
                threshold=Config.get('AI_THRESHOLD', 5.5),
                memory_context=safe_memory
            ).replace("[ORACLE_RULES_PLACEHOLDER]", oracle_rules)
            prompt += normalization_hint
        else:
            # Public Lite Version (General ICT logic)
            entry_val = setup.get('entry') or setup.get('entry_price', 0.0)
            sl_val = setup.get('stop_loss', 0.0)
            tp_val = setup.get('target') or setup.get('take_profit', 0.0)
            prompt = f"""
            YOU ARE AN INSTITUTIONAL RISK MANAGER (ICT PHILOSOPHY).
            Analyze this trade setup using Standard ICT concepts (PO3, FVG, SMT).
            
            ### GOAL: Identify if this setup aligns with institutional expansion or retail inducement.
            
            - Symbol: {setup.get('symbol')}
            - Pattern: {setup.get('pattern', 'SMC Logic')}
            - Entry: ${entry_val:,.2f} | Stop Loss: ${sl_val:,.2f} | Target: ${tp_val:,.2f}
            - SMT Strength: {setup.get('smt_strength', 0)}
            - Regime: {regime_detail}
            - Confluences: {oracle_rules}
            
            ### STRATEGY-AWARE EVALUATION DIRECTIVE:
            1. LIQUIDITY SWEEP & JUDAS FADE: Focus on wick absorption, volume spike, and trapping breakout retail. Do NOT penalize for counter-trend entry.
            2. TREND EXPANSION & DISPLACEMENT: Focus on strong SMT divergence, displacement body (>1.5x ATR), and HTF alignment. Penalize counter-trend entries.

            ### FORENSIC AUDIT OBJECTIVES:
            1. Structural Flaw Check: Is this a genuine institutional entry or retail trap?
            2. News Check: Are high-impact news catalysts (CPI, FOMC, NFP) within 30 min?
            3. Discipline Gate: Does this meet strict SMC criteria (valid FVG, POI, SMT)?
            4. Stop Loss & Spread Immunity: Verify the Stop Loss sits safely beyond micro-fractal wicks and broker spread noise (>0.35% for crypto).
            
            Return a JSON object in this exact schema:
            {{
                "live_execution": {{
                    "score": <0.0-10.0 numerical rating>,
                    "verdict": "<FLOW_GO if score >= {Config.get('AI_THRESHOLD', 5.5)} else REJECTED>",
                    "reasoning": "<Concise forensic analysis of structural flaws, SMT, volume, and regime>",
                    "execution_logic": "<Clear entry, SL, TP execution notes>",
                    "discipline_check": "<PASSED or FAILED with specific rule violation>"
                }},
                "shadow_optimizer": {{
                    "suggested_risk_multiplier": <1.0 for normal, 0.5 for probe, 1.25 for high confidence>,
                    "regime_classification": "<Current detected regime>",
                    "alpha_delta_prediction": "<Expected edge impact>",
                    "slippage_estimate": "<Slippage estimate>",
                    "optimization_reasoning": "<Optimization logic>"
                }}
            }}
            """

        if image_path:
            if self.sovereign_vision:
                prompt += self.sovereign_vision
            else:
                prompt += "\nAnalysis of attached chart image requested for ICT confluence."

        try:
            # For development, return simulated result if key is 'MOCK'
            if self.api_key == "MOCK":
                return {
                    "live_execution": {
                        "score": 9.2,
                        "verdict": "FLOW_GO",
                        "reasoning": f"MOCK: {setup.get('pattern')} confirmed with Strong SMT (>0.35) and Deep Discount. Sniper criteria met.",
                        "execution_logic": "Execute at FVG with 1:3 RR",
                        "discipline_check": "Institutional Grade Setup"
                    },
                    "shadow_optimizer": {
                        "suggested_risk_multiplier": 1.25,
                        "regime_classification": regime,
                        "alpha_delta_prediction": "+20% vs control (High Precision)",
                        "slippage_estimate": f"{slippage_info.get('slippage_pct', 'N/A')}%",
                        "optimization_reasoning": "Score >9.0 + Low-Volatility = Aggressive 1.25x Size"
                    }
                }

            contents = [prompt]
            if image_path and os.path.exists(image_path):
                from PIL import Image
                img = Image.open(image_path)
                contents.append(img)

            # NEW-GEN MULTI-MODEL HUB
            result = self.hub.analyze_setup(prompt, image_path)
            
            # Validate structure (ensure both tracks exist)
            if 'live_execution' not in result or 'shadow_optimizer' not in result:
                print("⚠️ AI returned incomplete dual-track structure. Using fallback.")
                result = self.hard_logic_audit(setup, df)
            
            # ── 👻 SHADOW VISUAL VECTOR OBSERVATION (ZERO LIVE CAPITAL RISK) ──
            # Relegated to 100% shadow observation telemetry. Does NOT modify live execution score or verdict.
            try:
                from src.engines.visual_vector_engine import VisualVectorEngine
                v_engine = VisualVectorEngine()
                q_vec = v_engine.extract_geometric_features(df, setup=setup)
                active_regime = setup.get('regime', setup.get('regime_type', 'UNKNOWN'))
                v_eval = v_engine.evaluate_visual_precedent(
                    q_vec, 
                    symbol=setup.get('symbol', 'BTC/USD'), 
                    direction=setup.get('direction', 'LONG'),
                    regime_type=active_regime
                )
                
                result['shadow_visual_vector'] = v_eval
                if 'shadow_optimizer' in result and isinstance(result['shadow_optimizer'], dict):
                    result['shadow_optimizer']['visual_vector'] = v_eval
                    
                rec = v_eval.get('recommendation', 'NEUTRAL')
                print(f"👻 [SHADOW VISUAL VECTOR OBSERVATION] {setup.get('symbol', 'BTC/USD')} {setup.get('direction', 'LONG')}: {v_eval.get('key_reason', '')} | Rec: {rec} (Telemetry only / 0% live impact).")
            except Exception as _vec_err:
                logger.debug(f"Visual Vector Shadow Telemetry fallback: {_vec_err}")

            return result
                
        except Exception as e:
            print(f"⚠️ AI Hub Failure: {e}. Switching to HARD LOGIC FALLBACK.")
            return self.hard_logic_audit(setup, df)

    def get_visual_bias(self, image_path):
        """
        VISION AUDIT: Determines Trend Bias from Chart Image.
        Returns: +1 (Bullish), -1 (Bearish), 0 (Neutral)
        """
        try:
            return self.hub.get_visual_bias(image_path)
        except Exception as e:
            print(f"⚠️ Visual Bias Check Failed: {e}")
            return 0

def validate_setup(setup, sentiment, whales, image_path=None, df=None, exchange=None, memory_context=None, hurst_exponent=None, guard_trust_score=None):
    """
    Main entry point for trade validation with dual-track analysis.
    
    Args:
        setup: Trade setup dict
        sentiment: Market sentiment data
        whales: Whale activity data
        image_path: Optional chart image path
        df: Optional dataframe for regime detection
        exchange: Optional CCXT exchange for slippage estimation
        memory_context: Optional historical context from RAG
        hurst_exponent: Optional Hurst exponent for market regime
        guard_trust_score: Optional trust score from GuardEngine
    
    Returns:
        dict: Dual-track analysis result
    """
    validator = AIValidator()
    return validator.analyze_trade(setup, sentiment, whales, image_path, df, exchange, memory_context, hurst_exponent, guard_trust_score)
