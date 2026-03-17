"""
castor/agent_tools.py — Extended agent tools for the harness tool loop.

Registers 5 new tools into ToolRegistry:
  - web_search(query, num_results)     — Brave/DuckDuckGo web search
  - get_telemetry()                    — full sensor snapshot
  - recall_episode(query, k)           — episodic memory RAG
  - send_rcan_message(rrn, message)    — RCAN chat to peer robot
  - query_local_knowledge(query, k)   — RAG over local knowledge docs

All tools are P66 auto-approved (read-only / communication only).
Physical tools (move, grip) remain in tools.py.

Usage::

    from castor.agent_tools import register_agent_tools
    register_agent_tools(tool_registry)
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from castor.tools import ToolRegistry

logger = logging.getLogger("OpenCastor.AgentTools")

__all__ = ["register_agent_tools"]


def register_agent_tools(registry: ToolRegistry) -> None:
    """Register all agent tools into the given ToolRegistry."""
    registry.register(
        name="web_search",
        fn=web_search,
        description=(
            "Search the web for current information, facts, specs, or how-to guides. "
            "Returns top results with title, URL, and snippet."
        ),
        parameters={
            "query": {"type": "string", "description": "Search query", "required": True},
            "num_results": {
                "type": "integer",
                "description": "Number of results (1-5)",
                "required": False,
            },
        },
        returns="array",
    )
    registry.register(
        name="get_telemetry",
        fn=get_telemetry,
        description=(
            "Get the robot's current sensor and system status: battery, CPU temp, "
            "distance to nearest obstacle, motor status, uptime, camera availability."
        ),
        parameters={},
        returns="object",
    )
    registry.register(
        name="recall_episode",
        fn=recall_episode,
        description=(
            "Search the robot's episodic memory for relevant past experiences. "
            "Returns summaries of past interactions matching the query."
        ),
        parameters={
            "query": {
                "type": "string",
                "description": "What to search for in memory",
                "required": True,
            },
            "k": {
                "type": "integer",
                "description": "Number of results (default 3)",
                "required": False,
            },
        },
        returns="array",
    )
    registry.register(
        name="send_rcan_message",
        fn=send_rcan_message,
        description=(
            "Send an RCAN chat message to a peer robot and wait for its response. "
            "Use to consult, coordinate with, or delegate to another robot in the fleet."
        ),
        parameters={
            "rrn": {
                "type": "string",
                "description": "Target robot RRN (e.g. RRN-000000000005)",
                "required": True,
            },
            "message": {"type": "string", "description": "Message to send", "required": True},
            "timeout_s": {
                "type": "number",
                "description": "Timeout in seconds (default 10)",
                "required": False,
            },
        },
        returns="object",
    )
    registry.register(
        name="query_local_knowledge",
        fn=query_local_knowledge,
        description=(
            "Search locally stored documents (manuals, logs, notes) using semantic search. "
            "Documents are stored in ~/.config/opencastor/knowledge/"
        ),
        parameters={
            "query": {"type": "string", "description": "What to search for", "required": True},
            "k": {
                "type": "integer",
                "description": "Number of results (default 5)",
                "required": False,
            },
        },
        returns="array",
    )
    registry.register(
        name="share_config_with_peer",
        fn=share_config_with_peer,
        description=(
            "Share a RCAN config file with a peer robot via RCAN CONFIG_SHARE message. "
            "The peer must confirm before installing. Requires R2RAM chat-level consent."
        ),
        parameters={
            "peer_rrn": {
                "type": "string",
                "description": "Target robot RRN (e.g. RRN-000000000001)",
                "required": True,
            },
            "config_path": {
                "type": "string",
                "description": "Path to the .rcan.yaml file to share",
                "required": True,
            },
            "title": {
                "type": "string",
                "description": "Optional display title",
                "required": False,
            },
        },
        returns="object",
    )
    logger.info(
        "AgentTools: registered web_search, get_telemetry, recall_episode, "
        "send_rcan_message, query_local_knowledge, share_config_with_peer"
    )


# ── Tool implementations ──────────────────────────────────────────────────────


def web_search(query: str = "", num_results: int = 3) -> list[dict]:
    """Search the web using Brave API or DuckDuckGo fallback."""
    num_results = max(1, min(5, int(num_results or 3)))
    if not query.strip():
        return [{"error": "Empty query"}]

    # Try Brave Search API first
    brave_key = os.environ.get("BRAVE_API_KEY", "")
    if brave_key:
        try:
            return _brave_search(query, num_results, brave_key)
        except Exception as exc:
            logger.warning("Brave search failed, trying DuckDuckGo: %s", exc)

    # DuckDuckGo HTML fallback (no key needed)
    try:
        return _ddg_search(query, num_results)
    except Exception as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)
        return [{"error": f"Search unavailable: {exc}", "query": query}]


def _brave_search(query: str, num_results: int, api_key: str) -> list[dict]:
    """Brave Search API."""
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
        {"q": query, "count": num_results, "text_decorations": "false"}
    )
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read())
    results = []
    for item in data.get("web", {}).get("results", [])[:num_results]:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", ""),
            }
        )
    return results


def _ddg_search(query: str, num_results: int) -> list[dict]:
    """DuckDuckGo instant answer API (limited but no key needed)."""
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
        {
            "q": query,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1,
            "no_redirect": 1,
        }
    )
    req = urllib.request.Request(url, headers={"User-Agent": "OpenCastor/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read())

    results = []
    # Abstract (top result)
    if data.get("AbstractText"):
        results.append(
            {
                "title": data.get("Heading", query),
                "url": data.get("AbstractURL", ""),
                "snippet": data["AbstractText"][:400],
            }
        )
    # Related topics
    for topic in data.get("RelatedTopics", [])[: num_results - len(results)]:
        if isinstance(topic, dict) and topic.get("Text"):
            results.append(
                {
                    "title": topic.get("Text", "")[:60],
                    "url": topic.get("FirstURL", ""),
                    "snippet": topic.get("Text", "")[:300],
                }
            )
    if not results:
        results.append(
            {"title": "No results", "url": "", "snippet": f"No results found for: {query}"}
        )
    return results[:num_results]


def get_telemetry() -> dict:
    """Return the robot's current sensor and system telemetry."""
    try:
        from castor.main import get_shared_fs

        fs = get_shared_fs()
        if fs and hasattr(fs, "proc"):
            snap = fs.proc.snapshot()
            if isinstance(snap, dict):
                return snap
    except Exception as exc:
        logger.debug("Telemetry from shared FS failed: %s", exc)

    # Fallback: basic system info
    try:
        import psutil

        return {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_percent": psutil.virtual_memory().percent,
            "uptime_s": int(time.time() - psutil.boot_time()),
            "source": "psutil_fallback",
        }
    except ImportError:
        pass

    return {"status": "telemetry_unavailable"}


def recall_episode(query: str = "", k: int = 3) -> list[dict]:
    """Query episodic memory for relevant past experiences."""
    k = max(1, min(10, int(k or 3)))
    if not query.strip():
        return []
    try:
        from castor.memory.episode import EpisodeStore

        store = EpisodeStore.get_default()
        if store is None:
            return []
        results = store.search(query, k=k)
        return results or []
    except Exception as exc:
        logger.debug("Episodic recall failed: %s", exc)
        return []


def send_rcan_message(
    rrn: str = "",
    message: str = "",
    timeout_s: float = 10.0,
) -> dict:
    """Send an RCAN chat message to a peer robot and return its response."""
    if not rrn or not message:
        return {"error": "rrn and message are required"}

    timeout_s = max(1.0, min(30.0, float(timeout_s or 10.0)))

    # Try direct RCAN HTTP transport
    try:
        return _send_rcan_http(rrn, message, timeout_s)
    except Exception as exc:
        logger.warning("RCAN HTTP failed: %s", exc)

    # Try Firebase relay (if bridge is active)
    try:
        return _send_rcan_firebase(rrn, message, timeout_s)
    except Exception as exc:
        logger.warning("RCAN Firebase failed: %s", exc)
        return {"error": f"Could not reach {rrn}: {exc}", "rrn": rrn}


def _send_rcan_http(rrn: str, message: str, timeout_s: float) -> dict:
    """Send via direct HTTP to peer robot gateway."""
    # Resolve peer URL from fleet config or known RRNs
    peer_urls = _get_peer_urls()
    peer_url = peer_urls.get(rrn)
    if not peer_url:
        raise ValueError(f"No HTTP endpoint known for {rrn}")

    req = urllib.request.Request(
        f"{peer_url.rstrip('/')}/api/command",
        data=json.dumps({"instruction": message, "scope": "chat"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read())
    return {
        "response": data.get("raw_text", ""),
        "from_rrn": rrn,
        "latency_ms": round((time.time() - t0) * 1000, 1),
    }


def _send_rcan_firebase(rrn: str, message: str, timeout_s: float) -> dict:
    """Send via Firebase Firestore (bridge relay)."""
    import time as _time
    import uuid

    try:
        from firebase_admin import firestore as _fs
    except ImportError:
        raise RuntimeError("firebase_admin not available") from None

    db = _fs.client()
    cmd_id = str(uuid.uuid4())
    t0 = _time.time()

    # Write command to peer's Firestore commands collection
    db.collection("robots").document(rrn).collection("commands").document(cmd_id).set(
        {
            "id": cmd_id,
            "type": "CHAT",
            "from_rrn": _get_own_rrn(),
            "payload": {"instruction": message},
            "loa": 2,
            "timestamp": _time.time(),
        }
    )

    # Poll for response
    deadline = t0 + timeout_s
    while _time.time() < deadline:
        _time.sleep(0.5)
        resp_doc = (
            db.collection("robots").document(rrn).collection("responses").document(cmd_id).get()
        )
        if resp_doc.exists:
            data = resp_doc.to_dict()
            return {
                "response": data.get("raw_text", ""),
                "from_rrn": rrn,
                "latency_ms": round((_time.time() - t0) * 1000, 1),
            }

    return {"error": f"Timeout waiting for response from {rrn}", "rrn": rrn}


def query_local_knowledge(query: str = "", k: int = 5) -> list[dict]:
    """RAG over local knowledge files in ~/.config/opencastor/knowledge/."""
    k = max(1, min(20, int(k or 5)))
    if not query.strip():
        return []

    knowledge_dir = os.path.expanduser("~/.config/opencastor/knowledge/")
    if not os.path.isdir(knowledge_dir):
        return []

    # Collect text chunks from all supported files
    chunks: list[dict] = []
    for fname in os.listdir(knowledge_dir):
        fpath = os.path.join(knowledge_dir, fname)
        if not os.path.isfile(fpath):
            continue
        ext = fname.lower().rsplit(".", 1)[-1]
        if ext not in ("txt", "md", "json"):
            continue
        try:
            text = open(fpath, encoding="utf-8", errors="ignore").read()
            # Split into ~500-char chunks
            for i in range(0, len(text), 500):
                chunk = text[i : i + 500].strip()
                if chunk:
                    chunks.append({"source": fname, "chunk": chunk})
        except Exception:
            continue

    if not chunks:
        return []

    # Score by keyword overlap
    query_words = set(query.lower().split())
    scored = []
    for c in chunks:
        chunk_words = set(c["chunk"].lower().split())
        overlap = len(query_words & chunk_words)
        if overlap > 0:
            scored.append((overlap, c))

    # Try embedding-based ranking if available
    try:
        from castor.learner.embedding_interpreter import EmbeddingInterpreter

        interp = EmbeddingInterpreter.get_default()
        if interp:
            q_emb = interp.embed(query)
            scored = []
            for c in chunks:
                c_emb = interp.embed(c["chunk"])
                sim = _cosine_sim(q_emb, c_emb)
                scored.append((sim, c))
    except Exception:
        pass

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {"source": c["source"], "chunk": c["chunk"], "relevance": round(float(score), 3)}
        for score, c in scored[:k]
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_own_rrn() -> str:
    try:
        from castor.main import get_shared_fs

        fs = get_shared_fs()
        if fs:
            return getattr(fs, "rrn", "") or ""
    except Exception:
        pass
    return ""


def _get_peer_urls() -> dict[str, str]:
    """Return known RRN → HTTP base URL mappings from config or env."""
    try:
        from castor.main import get_shared_fs

        fs = get_shared_fs()
        if fs and hasattr(fs, "config"):
            peers = fs.config.get("fleet", {}).get("peers", [])
            return {p["rrn"]: p["url"] for p in peers if "rrn" in p and "url" in p}
    except Exception:
        pass
    # Hardcoded known peers (fallback)
    return {
        "RRN-000000000001": "http://127.0.0.1:8001",  # Bob
        "RRN-000000000005": "http://alex.local:8000",  # Alex
    }


def _cosine_sim(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── CONFIG_SHARE agent tool ───────────────────────────────────────────────────

_CONFIG_SHARE_SECRET_PATTERNS = [
    r"api[_-]?key\s*:\s*\S+",
    r"token\s*:\s*\S+",
    r"password\s*:\s*\S+",
    r"secret\s*:\s*\S+",
    r"private[_-]?key\s*:\s*\S+",
    r"credentials\s*:\s*\S+",
]


def _scrub_config_content(content: str) -> str:
    """Remove secret values from RCAN config content before sharing."""
    import re

    scrubbed = content
    for pattern in _CONFIG_SHARE_SECRET_PATTERNS:
        scrubbed = re.sub(pattern, "[REDACTED]", scrubbed, flags=re.IGNORECASE)
    return scrubbed


def share_config_with_peer(
    peer_rrn: str = "",
    config_path: str = "",
    title: str = "",
) -> dict:
    """Share a RCAN config file with a peer robot via RCAN CONFIG_SHARE message.

    This sends the config content to the peer's bridge for operator review.
    The peer will NOT auto-install — they must confirm via their operator interface.
    Requires R2RAM chat-level consent from the peer.

    Args:
        peer_rrn: Target robot's RRN (e.g., 'RRN-000000000001').
        config_path: Path to the .rcan.yaml file to share.
        title: Optional display title for the config.

    Returns:
        Dict with 'success', 'message', and 'peer_rrn' keys.
    """
    import json
    from pathlib import Path

    if not peer_rrn:
        return {"success": False, "message": "peer_rrn is required"}
    if not config_path:
        return {"success": False, "message": "config_path is required"}

    config_file = Path(config_path).expanduser()
    if not config_file.exists():
        return {"success": False, "message": f"Config file not found: {config_path}"}

    content = config_file.read_text()
    scrubbed = _scrub_config_content(content)
    filename = config_file.name

    payload = {
        "type": "CONFIG_SHARE",
        "params": {
            "config_bundle": scrubbed,
            "filename": filename,
            "title": title or filename,
            "from_rrn": _get_own_rrn(),
        },
    }

    result = send_rcan_message(rrn=peer_rrn, message=json.dumps(payload), timeout_s=15.0)
    if result.get("status") == "sent":
        return {
            "success": True,
            "message": f"Config '{filename}' shared with {peer_rrn}. Awaiting peer confirmation.",
            "peer_rrn": peer_rrn,
        }
    return {
        "success": False,
        "message": f"Failed to reach peer {peer_rrn}: {result.get('error', 'unknown error')}",
        "peer_rrn": peer_rrn,
    }
