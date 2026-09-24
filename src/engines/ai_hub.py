import os
import json
import logging
from typing import Optional, List, Any, Dict
from dotenv import load_dotenv

load_dotenv(".env.local")
load_dotenv(".env")

from google import genai
import anthropic
import openai
from src.core.config import Config
from src.engines.local_llm_handler import LocalLLMHandler

logger = logging.getLogger(__name__)

class SovereignAIHub:
    """
    Unified interface for multiple AI providers (Gemini, Claude, GPT).
    Provides automatic failover to ensure analysis uptime.
    """
    def __init__(self):
        # Together AI (Direct Integration)
        self.together_key = os.environ.get("TOGETHER_API_KEY")
        self.together_model = os.environ.get("TOGETHER_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo")
        if self.together_key:
            self.together_client = openai.OpenAI(
                api_key=self.together_key,
                base_url="https://api.together.xyz/v1"
            )
        else:
            self.together_client = None

        # OpenRouter (Unified Provider)
        self.openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        self.openrouter_model = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")
        if self.openrouter_key:
            self.openrouter_client = openai.OpenAI(
                api_key=self.openrouter_key,
                base_url="https://openrouter.ai/api/v1"
            )
        else:
            self.openrouter_client = None

        # Gemini (Primary)
        self.gemini_key = os.environ.get("GEMINI_API_KEY")
        self.gemini_client = genai.Client(api_key=self.gemini_key) if self.gemini_key else None
        
        # Anthropic (Secondary)
        self.anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        self.anthropic_client = anthropic.Anthropic(api_key=self.anthropic_key) if self.anthropic_key else None
        
        # OpenAI (Tertiary)
        self.openai_key = os.environ.get("OPENAI_API_KEY")
        self.openai_client = openai.OpenAI(api_key=self.openai_key) if self.openai_key else None
        
        # Local (Last Resort)
        self.local_handler = LocalLLMHandler()

    @property
    def has_ai(self) -> bool:
        """Returns True if any AI provider is available (including local)."""
        return any([
            self.together_client,
            self.openrouter_client,
            self.gemini_client,
            self.anthropic_client,
            self.openai_client,
            self.local_handler.is_available()
        ])

    def analyze_setup(self, prompt: str, image_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Tries to analyze the setup using available providers in priority order.
        Together AI is strictly reserved for SFT fine-tuning jobs, not routine inference.
        """
        # PRIORITY 1: OpenRouter (Unified API key check)
        if self.openrouter_client:
            try:
                return self._analyze_with_openrouter(prompt, image_path)
            except Exception as e:
                logger.warning(f"⚠️ OpenRouter analysis failed: {e}. Falling back to Gemini...")


        # PRIORITY 1: Gemini
        if self.gemini_client:
            try:
                return self._analyze_with_gemini(prompt, image_path)
            except Exception as e:
                logger.warning(f"⚠️ Gemini analysis failed: {e}. Falling back to Claude...")

        # PRIORITY 2: Claude
        if self.anthropic_client:
            try:
                return self._analyze_with_claude(prompt, image_path)
            except Exception as e:
                logger.warning(f"⚠️ Claude analysis failed: {e}. Falling back to GPT...")

        # PRIORITY 3: GPT
        if self.openai_client:
            try:
                return self._analyze_with_gpt(prompt, image_path)
            except Exception as e:
                logger.warning(f"⚠️ GPT analysis failed: {e}. Falling back to Local...")

        # PRIORITY 4: Local LLM (Stub)
        try:
            return self._analyze_with_local(prompt, image_path)
        except Exception as e:
            logger.error(f"💥 All AI providers failed: {e}")
            raise Exception("Total AI Infrastructure Failure")

    def _analyze_with_together(self, prompt: str, image_path: Optional[str]) -> Dict[str, Any]:
        """Together AI Implementation."""
        content = [{"type": "text", "text": prompt}]
        
        if image_path and os.path.exists(image_path):
            import base64
            with open(image_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                content.insert(0, {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded_string}"}
                })

        response = self.together_client.chat.completions.create(
            model=self.together_model,
            messages=[{"role": "user", "content": content}],
            max_tokens=1024
        )
        return self._parse_json_response(response.choices[0].message.content, provider=f"Together/{self.together_model}")

    def _analyze_with_openrouter(self, prompt: str, image_path: Optional[str]) -> Dict[str, Any]:
        """OpenRouter Implementation."""
        content = [{"type": "text", "text": prompt}]
        
        if image_path and os.path.exists(image_path):
            import base64
            with open(image_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                content.insert(0, {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded_string}"}
                })

        response = self.openrouter_client.chat.completions.create(
            model=self.openrouter_model,
            messages=[{"role": "user", "content": content}],
            max_tokens=1024,
            extra_headers={
                "HTTP-Referer": "https://sovereignSMC.com",
                "X-Title": "Sovereign SMC"
            }
        )
        return self._parse_json_response(response.choices[0].message.content, provider=f"OpenRouter/{self.openrouter_model}")

    def _analyze_with_gemini(self, prompt: str, image_path: Optional[str]) -> Dict[str, Any]:
        """Gemini Pro / Flash Implementation."""
        contents = [prompt]
        if image_path and os.path.exists(image_path):
            from PIL import Image
            contents.append(Image.open(image_path))
        
        # Updated model priority: 2.5-flash, 1.5-flash, 1.5-pro
        for model in ['gemini-2.5-flash', 'gemini-1.5-flash', 'gemini-1.5-pro']:

            try:
                response = self.gemini_client.models.generate_content(
                    model=model,
                    contents=contents,
                    config={
                        'max_output_tokens': 800
                    }
                )
                from src.core.token_tracker import track_response_tokens
                track_response_tokens(response, model_name=model)
                return self._parse_json_response(response.text, provider=f"Gemini/{model}")
            except Exception as e:
                logger.warning(f"Gemini model {model} failed: {e}")
                continue
        raise Exception("All Gemini models failed")

    def _analyze_with_claude(self, prompt: str, image_path: Optional[str]) -> Dict[str, Any]:
        """Claude 3.5 Sonnet Implementation."""
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
        
        if image_path and os.path.exists(image_path):
            import base64
            with open(image_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                messages[0]["content"].insert(0, {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": encoded_string,
                    },
                })

        response = self.anthropic_client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=1024,
            messages=messages
        )
        return self._parse_json_response(response.content[0].text, provider="Claude")

    def _analyze_with_gpt(self, prompt: str, image_path: Optional[str]) -> Dict[str, Any]:
        """GPT-4o Implementation."""
        content = [{"type": "text", "text": prompt}]
        
        if image_path and os.path.exists(image_path):
            import base64
            with open(image_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                content.insert(0, {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded_string}"}
                })

        response = self.openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            max_tokens=1024
        )
        return self._parse_json_response(response.choices[0].message.content, provider="GPT")

    def _analyze_with_local(self, prompt: str, image_path: Optional[str]) -> Dict[str, Any]:
        """Local SLM (MLX LoRA) Implementation."""
        if self.local_handler.is_available():
             text = self.local_handler.analyze(prompt, image_path)
             return self._parse_json_response(text, provider="Local-LLM")
        raise Exception("Local LLM unavailable")

    def get_visual_bias(self, image_path: str) -> int:
        """
        Vision Audit: Determines Trend Bias from Chart Image.
        Returns: +1 (Bullish), -1 (Bearish), 0 (Neutral)
        """
        if not image_path or not os.path.exists(image_path):
            return 0
            
        prompt = "Determine the market trend bias from this chart. Return ONLY 'BULLISH', 'BEARISH', or 'NEUTRAL'."
        
        # Priority 0: Together AI
        if self.together_client:
            try:
                result = self._analyze_with_together(prompt, image_path)
                text = str(result).upper()
                if "BULLISH" in text: return 1
                if "BEARISH" in text: return -1
            except: pass

        # Priority 1: OpenRouter
        if self.openrouter_client:
            try:
                result = self._analyze_with_openrouter(prompt, image_path)
                text = str(result).upper()
                if "BULLISH" in text: return 1
                if "BEARISH" in text: return -1
            except: pass

        # Priority: Gemini (Vision is standard)
        if self.gemini_client:
            try:
                from PIL import Image
                img = Image.open(image_path)
                for model in ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-2.0-flash-lite']:
                    try:
                        response = self.gemini_client.models.generate_content(
                            model=model,
                            contents=[prompt, img],
                            config={
                                'max_output_tokens': 10
                            }
                        )
                        from src.core.token_tracker import track_response_tokens
                        track_response_tokens(response, model_name=model)
                        text = response.text.upper()
                        if "BULLISH" in text: return 1
                        if "BEARISH" in text: return -1
                        return 0
                    except: continue
            except: pass
            
        # Fallback: Claude (Vision is standard)
        if self.anthropic_client:
            try:
                # Reuse _analyze_with_claude's logic for encoding
                result = self._analyze_with_claude(prompt, image_path)
                text = str(result).upper()
                if "BULLISH" in text: return 1
                if "BEARISH" in text: return -1
            except: pass

        return 0

    def _parse_json_response(self, text: str, provider: str) -> Dict[str, Any]:
        """Cleanly extracts JSON or structured score/reasoning from LLM response."""
        import re, json
        # 1. Clean markdown code blocks
        clean_text = text.replace("```json", "").replace("```", "").strip()
        json_match = re.search(r'\{.*\}', clean_text, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group())
                result['provider'] = provider
                return result
            except Exception:
                pass
        
        # 2. Fallback: Extract score and reasoning from raw text
        score_match = re.search(r'(?:score|grade|rating)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)', text, re.IGNORECASE)
        score = float(score_match.group(1)) if score_match else 7.5
        return {
            "score": score,
            "reasoning": text.strip(),
            "provider": provider
        }

