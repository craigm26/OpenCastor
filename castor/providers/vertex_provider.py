import logging
import os
import time
from typing import Iterator

from .base import BaseProvider, Thought

logger = logging.getLogger("OpenCastor.VertexAI")

try:
    from google import genai
    from google.genai import types

    HAS_VERTEX = True
except ImportError:
    HAS_VERTEX = False


class VertexAIProvider(BaseProvider):
    """Google Vertex AI adapter for running Gemini models via Google Cloud.

    Authenticates via service account (GOOGLE_APPLICATION_CREDENTIALS) or
    Application Default Credentials (ADC via ``gcloud auth application-default login``).
    Suitable for enterprise deployments that require org-level quotas, VPC-SC
    perimeters, or service-account-based access control.

    Required env vars:
        VERTEX_PROJECT  -- GCP project ID (required)
        VERTEX_LOCATION -- GCP region (default: us-central1)
        GOOGLE_APPLICATION_CREDENTIALS -- path to service account JSON (optional;
                                          falls back to ADC if not set)

    Model names on Vertex AI use the ``-001`` (or similar) versioned suffix, e.g.
    ``gemini-2.0-flash-001``.
    """

    DEFAULT_MODEL = "gemini-2.0-flash-001"
    DEFAULT_LOCATION = "us-central1"

    def __init__(self, config: dict):
        if not HAS_VERTEX:
            raise ValueError(
                "google-genai SDK not installed. "
                "Install it with: pip install opencastor[vertex]"
            )

        # Resolve model before calling super().__init__ so self.model_name is
        # available if the parent ever needs it during construction.
        if "model" not in config:
            config = {**config, "model": self.DEFAULT_MODEL}

        super().__init__(config)

        self.project = os.getenv("VERTEX_PROJECT") or config.get("vertex_project")
        if not self.project:
            raise ValueError(
                "VERTEX_PROJECT environment variable is required for Vertex AI. "
                "Set it to your GCP project ID."
            )

        self.location = (
            os.getenv("VERTEX_LOCATION")
            or config.get("vertex_location")
            or self.DEFAULT_LOCATION
        )

        # google-genai uses GOOGLE_APPLICATION_CREDENTIALS automatically (ADC chain).
        self._client = genai.Client(
            vertexai=True,
            project=self.project,
            location=self.location,
        )

        logger.info(
            "VertexAIProvider initialised: project=%s location=%s model=%s",
            self.project,
            self.location,
            self.model_name,
        )

    # ── Health check ─────────────────────────────────────────────────────────

    def health_check(self) -> dict:
        """Cheap health probe: list available models (no inference cost)."""
        t0 = time.time()
        try:
            # Consume only the first item to minimise latency.
            next(iter(self._client.models.list()), None)
            return {
                "ok": True,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": None,
            }
        except Exception as exc:
            return {
                "ok": False,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "error": str(exc),
            }

    # ── Core inference ────────────────────────────────────────────────────────

    def think(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Thought:
        """Send a frame + instruction to Vertex AI Gemini and return a Thought."""
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            return safety_block

        is_blank = not image_bytes or image_bytes == b"\x00" * len(image_bytes)

        try:
            if is_blank:
                messaging_ctx = self.build_messaging_prompt(surface=surface)
                contents = [f"{messaging_ctx}\n\nUser: {instruction}"]
            else:
                image_part = types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg",
                )
                contents = [instruction, image_part]

            response = self._client.models.generate_content(
                model=self.model_name,
                contents=contents,
            )

            text = self._extract_text(response)
            action = self._clean_json(text)

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
        except Exception as exc:
            logger.error("Vertex AI error: %s", exc)
            return Thought(f"Error: {exc}", None)

    # ── Streaming inference ───────────────────────────────────────────────────

    def think_stream(
        self,
        image_bytes: bytes,
        instruction: str,
        surface: str = "whatsapp",
    ) -> Iterator[str]:
        """Stream tokens from Vertex AI Gemini.

        Yields individual text chunks as they arrive from the API.
        """
        safety_block = self._check_instruction_safety(instruction)
        if safety_block is not None:
            yield safety_block.raw_text
            return

        is_blank = not image_bytes or image_bytes == b"\x00" * len(image_bytes)

        try:
            if is_blank:
                messaging_ctx = self.build_messaging_prompt(surface=surface)
                contents = [f"{messaging_ctx}\n\nUser: {instruction}"]
            else:
                image_part = types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg",
                )
                contents = [instruction, image_part]

            for chunk in self._client.models.generate_content_stream(
                model=self.model_name,
                contents=contents,
            ):
                try:
                    text = chunk.text
                    if text:
                        yield text
                except Exception:
                    continue
        except Exception as exc:
            logger.error("Vertex AI streaming error: %s", exc)
            yield f"Error: {exc}"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_text(self, response) -> str:
        """Extract text from a Vertex AI generate_content response."""
        try:
            return response.text
        except Exception:
            pass

        # Walk candidates/parts manually as a fallback.
        try:
            parts = response.candidates[0].content.parts
            text_parts = [
                part.text for part in parts if hasattr(part, "text") and part.text
            ]
            return "\n".join(text_parts) if text_parts else ""
        except Exception as exc:
            logger.warning("Could not extract text from Vertex AI response parts: %s", exc)
            return ""
