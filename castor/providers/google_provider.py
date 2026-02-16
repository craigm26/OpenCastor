import os
import logging

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.Google")


class GoogleProvider(BaseProvider):
    """Google Gemini adapter. Optimized for vision/multimodal tasks."""

    def __init__(self, config):
        super().__init__(config)
        import google.generativeai as genai

        api_key = os.getenv("GOOGLE_API_KEY") or config.get("api_key")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY not found in environment or config")
        genai.configure(api_key=api_key)

        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=self.system_prompt,
        )

    def think(self, image_bytes: bytes, instruction: str) -> Thought:
        image_part = {"mime_type": "image/jpeg", "data": image_bytes}

        try:
            response = self.model.generate_content([instruction, image_part])
            text = response.text
            action = self._clean_json(text)
            return Thought(text, action)
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            return Thought(f"Error: {e}", None)
