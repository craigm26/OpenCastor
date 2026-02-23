"""Fine-tuning export for OpenCastor.

Exports robot episode memory to HuggingFace-compatible dataset formats for
LLM fine-tuning.  Use collected episodes to teach any LLM to respond like
your robot's brain provider.

Supported formats:
    jsonl      — One JSON object per line (generic)
    alpaca     — Stanford Alpaca {instruction, input, output}
    sharegpt   — ShareGPT conversation {conversations: [{from, value}]}
    chatml     — OpenAI ChatML {messages: [{role, content}]}

Usage::

    from castor.finetune import EpisodeFinetuneExporter, export_episodes
    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()
    export_episodes(mem, "/tmp/robot_dataset.jsonl", fmt="chatml")

CLI::

    castor export-finetune --format chatml --output dataset.jsonl --limit 1000

REST API:
    GET /api/finetune/export?format=jsonl&limit=500
"""

import io
import json
import logging
from typing import Any, Dict, Iterator, Literal, Optional

logger = logging.getLogger("OpenCastor.Finetune")

ExportFormat = Literal["jsonl", "alpaca", "sharegpt", "chatml"]

_SYSTEM_MSG = (
    "You are a robot controller. Given a scene description or instruction, "
    "output a JSON action dict. Example: {\"action\": \"forward\", \"speed\": 0.5}"
)


def _episode_to_jsonl(ep: Dict[str, Any]) -> Dict[str, Any]:
    """Generic JSONL format: one episode per line."""
    return {
        "id": ep.get("id"),
        "instruction": ep.get("instruction", ""),
        "image_hash": ep.get("image_hash", ""),
        "response": ep.get("raw_text", ""),
        "action": ep.get("action"),
        "latency_ms": ep.get("latency_ms"),
        "timestamp": ep.get("timestamp"),
    }


def _episode_to_alpaca(ep: Dict[str, Any]) -> Dict[str, Any]:
    """Stanford Alpaca format: {instruction, input, output}."""
    return {
        "instruction": "You are a robot controller. Produce a JSON action for the given command.",
        "input": ep.get("instruction", ""),
        "output": ep.get("raw_text", ""),
    }


def _episode_to_sharegpt(ep: Dict[str, Any]) -> Dict[str, Any]:
    """ShareGPT format: {conversations: [{from: human|gpt, value: ...}]}."""
    return {
        "conversations": [
            {"from": "system", "value": _SYSTEM_MSG},
            {"from": "human", "value": ep.get("instruction", "")},
            {"from": "gpt", "value": ep.get("raw_text", "")},
        ]
    }


def _episode_to_chatml(ep: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAI ChatML format: {messages: [{role, content}]}."""
    return {
        "messages": [
            {"role": "system", "content": _SYSTEM_MSG},
            {"role": "user", "content": ep.get("instruction", "")},
            {"role": "assistant", "content": ep.get("raw_text", "")},
        ]
    }


_CONVERTERS = {
    "jsonl": _episode_to_jsonl,
    "alpaca": _episode_to_alpaca,
    "sharegpt": _episode_to_sharegpt,
    "chatml": _episode_to_chatml,
}


class EpisodeFinetuneExporter:
    """Convert EpisodeMemory records to fine-tuning dataset formats.

    Args:
        memory: An EpisodeMemory instance to read from.
    """

    def __init__(self, memory: Any = None):
        if memory is None:
            from castor.memory import EpisodeMemory

            self._mem = EpisodeMemory()
        else:
            self._mem = memory

    def iter_records(
        self,
        fmt: ExportFormat = "chatml",
        limit: int = 1000,
        min_latency_ms: Optional[float] = None,
        require_action: bool = False,
    ) -> Iterator[Dict[str, Any]]:
        """Yield converted episode records.

        Args:
            fmt: Output format.
            limit: Max episodes to export.
            min_latency_ms: Skip episodes slower than this threshold (outlier filter).
            require_action: If True, skip episodes with no parsed action.
        """
        converter = _CONVERTERS.get(fmt)
        if converter is None:
            raise ValueError(f"Unknown format '{fmt}'. Valid: {list(_CONVERTERS)}")

        episodes = self._mem.query_recent(limit=limit)
        exported = 0
        skipped = 0

        for ep in episodes:
            if require_action and not ep.get("action"):
                skipped += 1
                continue
            if min_latency_ms and (ep.get("latency_ms") or 0) > min_latency_ms:
                skipped += 1
                continue
            yield converter(ep)
            exported += 1

        logger.info(
            "FinetuneExporter: exported=%d skipped=%d fmt=%s", exported, skipped, fmt
        )

    def export_to_file(
        self,
        path: str,
        fmt: ExportFormat = "chatml",
        limit: int = 1000,
        **kwargs: Any,
    ) -> int:
        """Export episodes to a JSONL file.

        Args:
            path: Output file path.
            fmt: Export format.
            limit: Max episodes.
            **kwargs: Passed to iter_records.

        Returns:
            Number of records written.
        """
        count = 0
        with open(path, "w", encoding="utf-8") as f:
            for record in self.iter_records(fmt=fmt, limit=limit, **kwargs):
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
        logger.info("Exported %d records to %s (fmt=%s)", count, path, fmt)
        return count

    def export_to_bytes(
        self,
        fmt: ExportFormat = "chatml",
        limit: int = 1000,
        **kwargs: Any,
    ) -> bytes:
        """Export episodes to a bytes buffer (for HTTP download)."""
        buf = io.StringIO()
        for record in self.iter_records(fmt=fmt, limit=limit, **kwargs):
            buf.write(json.dumps(record, ensure_ascii=False) + "\n")
        return buf.getvalue().encode("utf-8")

    def stats(self, limit: int = 10000) -> Dict[str, Any]:
        """Return dataset statistics."""
        episodes = self._mem.query_recent(limit=limit)
        total = len(episodes)
        with_action = sum(1 for e in episodes if e.get("action"))
        latencies = [e.get("latency_ms") or 0 for e in episodes]
        avg_latency = sum(latencies) / max(len(latencies), 1)
        return {
            "total_episodes": total,
            "with_action": with_action,
            "without_action": total - with_action,
            "avg_latency_ms": round(avg_latency, 1),
            "formats": list(_CONVERTERS.keys()),
        }


def export_episodes(
    memory: Any,
    output_path: str,
    fmt: ExportFormat = "chatml",
    limit: int = 1000,
) -> int:
    """Convenience function: export episodes to a file.

    Returns number of records written.
    """
    return EpisodeFinetuneExporter(memory).export_to_file(
        output_path, fmt=fmt, limit=limit
    )
