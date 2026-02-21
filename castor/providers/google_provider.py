import logging
import os

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.Google")

# Models that support Agentic Vision (code_execution tool unlocks 5-10% vision boost)
# Think→Act→Observe loop: zooms, annotates, and visually grounds answers in evidence.
_AGENTIC_VISION_MODELS = {
    "gemini-3-flash-preview",
    "gemini-3-flash",
    "gemini-3-pro-preview",
    "gemini-3-pro",
}

# System prompt addendum injected for Agentic Vision models so the robot
# knows to leverage zooming/annotation for obstacle and scene analysis.
_AGENTIC_VISION_SYSTEM_ADDENDUM = """
You have Agentic Vision enabled. When analyzing camera frames:
- Use code execution to ZOOM IN on objects the depth sensor or object detector has flagged as uncertain.
- ANNOTATE the image with bounding boxes around obstacles or targets before deciding an action.
- For object identification (reading labels, serial numbers, hazard symbols), crop and inspect closely.
- Ground every navigation or manipulation decision in visual evidence — do not guess.
- Think step-by-step: formulate a plan, execute image operations, then observe before concluding.
"""


class GoogleProvider(BaseProvider):
    """Google Gemini adapter. Optimized for vision/multimodal and agentic tasks.

    Gemini 3 Flash and later support Agentic Vision: a Think→Act→Observe loop
    that combines visual reasoning with Python code execution. When one of these
    models is selected, code_execution is automatically added to the tools list
    and a system prompt addendum guides the robot to use it for obstacle/scene
    analysis.
    """

    def __init__(self, config):
        super().__init__(config)
        import google.generativeai as genai

        api_key = os.getenv("GOOGLE_API_KEY") or config.get("api_key")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY not found in environment or config")
        genai.configure(api_key=api_key)

        self._is_agentic_vision = self.model_name in _AGENTIC_VISION_MODELS
        # Allow explicit opt-in/opt-out via config
        if "agentic_vision" in config:
            self._is_agentic_vision = bool(config["agentic_vision"])

        # Build tools list
        tools = list(config.get("tools", []))
        if self._is_agentic_vision and "code_execution" not in tools:
            tools.append("code_execution")
            logger.info(
                f"Agentic Vision enabled for {self.model_name} — code_execution tool active"
            )

        # Augment system prompt for agentic vision
        system_instruction = self.system_prompt or ""
        if self._is_agentic_vision:
            system_instruction = system_instruction + _AGENTIC_VISION_SYSTEM_ADDENDUM

        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=system_instruction or None,
            tools=tools if tools else None,
        )

    def think(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Thought:
        is_blank = not image_bytes or image_bytes == b"\x00" * len(image_bytes)

        try:
            if is_blank:
                # Text-only: prepend messaging prompt (Gemini system_instruction is
                # set at model init, so we inject it as a leading text part here)
                messaging_ctx = self.build_messaging_prompt(surface=surface)
                response = self.model.generate_content(
                    [f"{messaging_ctx}\n\nUser: {instruction}"]
                )
            else:
                image_part = {"mime_type": "image/jpeg", "data": image_bytes}
                response = self.model.generate_content([instruction, image_part])

            # Agentic Vision responses may include executable_code + code_execution_result
            # parts before the final text. Collect all text parts.
            text = self._extract_text(response)
            action = self._clean_json(text)

            if self._is_agentic_vision:
                logger.debug(
                    f"Agentic Vision response parts: {[p.function_call or p.text[:40] if hasattr(p, 'text') else '...' for p in response.candidates[0].content.parts]}"
                )

            try:
                from castor.runtime_stats import record_api_call

                usage = getattr(response, "usage_metadata", None)
                record_api_call(
                    tokens_in=getattr(usage, "prompt_token_count", 0) if usage else 0,
                    tokens_out=getattr(usage, "candidates_token_count", 0) if usage else 0,
                    bytes_in=len(image_bytes) + len(instruction.encode()),
                    bytes_out=len(text.encode()),
                    model=self.model_name,
                )
            except Exception:
                pass
            return Thought(text, action)
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            return Thought(f"Error: {e}", None)

    def _extract_text(self, response) -> str:
        """Extract final text from response, skipping code/execution parts."""
        try:
            # Try the simple .text attribute first (works when no code exec parts)
            return response.text
        except Exception:
            pass

        # Walk parts manually for Agentic Vision multi-part responses
        try:
            parts = response.candidates[0].content.parts
            text_parts = []
            for part in parts:
                if hasattr(part, "text") and part.text:
                    text_parts.append(part.text)
            return "\n".join(text_parts) if text_parts else ""
        except Exception as e:
            logger.warning(f"Could not extract text from response parts: {e}")
            return ""
