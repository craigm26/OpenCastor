from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import json


class Thought:
    """Hardware-agnostic representation of a single AI reasoning step."""

    def __init__(self, raw_text: str, action: Optional[Dict] = None):
        self.raw_text = raw_text
        self.action = action  # The strict JSON command (e.g., {"linear": 0.5})
        self.confidence = 1.0


class BaseProvider(ABC):
    """Abstract base class for all AI model providers."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.model_name = config.get("model", "default-model")
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self, memory_context: str = "") -> str:
        """
        Constructs the 'Robotics Persona'.
        Forces the LLM to act as a low-latency controller, not a chatbot.

        If *memory_context* is provided (from the virtual filesystem's
        memory and context stores), it is appended so the brain has
        access to its own episodic/semantic/procedural memory.
        """
        base = (
            "You are the high-level controller for a robot running OpenCastor.\n"
            "Input: A video frame or telemetry data.\n"
            "Output: A STRICT JSON object defining the next physical action.\n\n"
            "Available Actions:\n"
            '- {"type": "move", "linear": float (-1.0 to 1.0), "angular": float (-1.0 to 1.0)}\n'
            '- {"type": "grip", "state": "open" | "close"}\n'
            '- {"type": "wait", "duration_ms": int}\n'
            '- {"type": "stop"}\n\n'
            "Do not output markdown. Do not explain yourself. Output ONLY valid JSON."
        )
        if memory_context:
            base += f"\n\n--- Robot Memory ---\n{memory_context}"
        return base

    @abstractmethod
    def think(self, image_bytes: bytes, instruction: str) -> Thought:
        """
        Takes raw image bytes and a text instruction.
        Returns a structured Thought object.
        """
        pass

    def _clean_json(self, text: str) -> Optional[Dict]:
        """Helper to extract JSON from messy LLM output."""
        try:
            clean = text.replace("```json", "").replace("```", "").strip()
            start = clean.find("{")
            end = clean.rfind("}") + 1
            if start != -1 and end != 0:
                return json.loads(clean[start:end])
            return None
        except Exception:
            return None
