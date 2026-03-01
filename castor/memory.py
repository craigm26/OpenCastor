"""
castor/memory.py — Persistent episode memory store (SQLite).

Logs every brain decision (thought, action, latency, image hash, timestamp)
to a local SQLite database so operators can inspect history and the learner
can query past context.

Usage::

    from castor.memory import EpisodeMemory

    mem = EpisodeMemory()  # defaults to ~/.castor/memory.db
    mem.log_episode(
        instruction="move forward",
        raw_thought='{"type":"move","linear":0.5}',
        action={"type": "move", "linear": 0.5},
        latency_ms=320.5,
        image_hash="abc123",
        outcome="ok",
        source="api",
    )
    episodes = mem.query_recent(limit=10)
    mem.export_jsonl("/tmp/episodes.jsonl")

Multi-modal memory (issue #267/#226):
    image_bytes can be passed to log_episode(); stored as a JPEG thumbnail
    (resized to 320x240 when cv2 is available, otherwise raw bytes).
    Retrieve via get_episode_image(ep_id) or list episodes that have images
    via episodes_with_images().
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Memory")

_DEFAULT_DB_DIR = Path.home() / ".castor"
_DEFAULT_DB_NAME = "memory.db"


class EpisodeMemory:
    """SQLite-backed episode memory store.

    Each *episode* is a single brain decision:
      instruction → thought → action → outcome.

    Thread-safe via ``check_same_thread=False`` and per-call connections.

    Args:
        db_path:      Full path to the SQLite database file.  Defaults to
                      ``~/.castor/memory.db`` (overridden by
                      ``CASTOR_MEMORY_DB`` env var or constructor argument).
        max_episodes: Automatically evict oldest episodes when the store
                      exceeds this count (FIFO).  ``0`` means unlimited.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        max_episodes: int = 10_000,
    ):
        env_path = os.getenv("CASTOR_MEMORY_DB")
        if db_path is None and env_path:
            db_path = env_path
        elif db_path is None:
            _DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
            db_path = str(_DEFAULT_DB_DIR / _DEFAULT_DB_NAME)

        self.db_path = db_path
        self.max_episodes = max_episodes
        self._init_db()

    # ── Internal ──────────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self):
        """Yield a connected, auto-committing SQLite connection."""
        con = sqlite3.connect(self.db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init_db(self) -> None:
        """Create the episodes table if it does not exist, and migrate schema."""
        ddl = """
        CREATE TABLE IF NOT EXISTS episodes (
            id           TEXT PRIMARY KEY,
            ts           REAL NOT NULL,
            instruction  TEXT,
            raw_thought  TEXT,
            action_json  TEXT,
            latency_ms   REAL,
            image_hash   TEXT,
            outcome      TEXT,
            source       TEXT DEFAULT 'loop',
            image_blob   BLOB DEFAULT NULL,
            tags         TEXT DEFAULT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ts ON episodes (ts DESC);
        """
        with self._conn() as con:
            con.executescript(ddl)
            # Migration: add image_blob column for multi-modal memory (#267/#226).
            # Silently ignored when the column already exists.
            try:
                con.execute("ALTER TABLE episodes ADD COLUMN image_blob BLOB DEFAULT NULL")
            except Exception:
                pass  # Column already exists
            # Migration: add tags column for episode tagging (#270).
            # Silently ignored when the column already exists.
            try:
                con.execute("ALTER TABLE episodes ADD COLUMN tags TEXT DEFAULT NULL")
            except Exception:
                pass  # Column already exists

    # ── Write ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _make_thumbnail(image_bytes: bytes) -> Optional[bytes]:
        """Resize *image_bytes* JPEG to 320×240 thumbnail using cv2 if available.

        Falls back to returning the raw bytes unchanged when cv2 is not
        installed or decoding fails.  Returns ``None`` when *image_bytes* is
        empty or ``None``.
        """
        if not image_bytes:
            return None
        try:
            import cv2
            import numpy as np

            buf = np.frombuffer(image_bytes, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                return image_bytes
            thumb = cv2.resize(img, (320, 240), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                return encoded.tobytes()
            return image_bytes
        except Exception:
            return image_bytes

    def log_episode(
        self,
        instruction: str = "",
        raw_thought: str = "",
        action: Optional[Dict] = None,
        latency_ms: float = 0.0,
        image_hash: str = "",
        outcome: str = "ok",
        source: str = "loop",
        image_bytes: Optional[bytes] = None,
        tags: Optional[List[str]] = None,
    ) -> str:
        """Insert a new episode.  Returns the generated episode UUID.

        Args:
            instruction:  Natural-language instruction sent to the brain.
            raw_thought:  Raw text output from the LLM.
            action:       Parsed action dict (serialised to JSON).
            latency_ms:   Round-trip latency for the brain call.
            image_hash:   Short SHA-256 hex digest of the source frame.
            outcome:      Freeform outcome label (default ``"ok"``).
            source:       Origin of the episode (``"loop"``, ``"api"``, …).
            image_bytes:  Raw JPEG bytes of the camera frame.  When provided
                          a 320×240 thumbnail is stored in ``image_blob``
                          (requires *opencv-python*; falls back to raw bytes).
            tags:         Optional list of user-defined tag strings stored as a
                          comma-separated value (e.g. ``["patrol", "outdoor"]``).
        """
        ep_id = str(uuid.uuid4())
        action_json = json.dumps(action) if action else None
        ts = time.time()
        thumbnail: Optional[bytes] = self._make_thumbnail(image_bytes) if image_bytes else None
        tags_str: Optional[str] = ",".join(tags) if tags else None
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO episodes
                    (id, ts, instruction, raw_thought, action_json,
                     latency_ms, image_hash, outcome, source, image_blob, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ep_id,
                    ts,
                    instruction[:512],
                    raw_thought[:2048],
                    action_json,
                    latency_ms,
                    image_hash,
                    outcome,
                    source,
                    thumbnail,
                    tags_str,
                ),
            )
        self._evict_if_needed()
        return ep_id

    def _evict_if_needed(self) -> None:
        """Delete oldest episodes when the store exceeds max_episodes."""
        if self.max_episodes <= 0:
            return
        with self._conn() as con:
            count = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            if count > self.max_episodes:
                excess = count - self.max_episodes
                con.execute(
                    """
                    DELETE FROM episodes WHERE id IN (
                        SELECT id FROM episodes ORDER BY ts ASC LIMIT ?
                    )
                    """,
                    (excess,),
                )

    def add_tags(self, episode_id: str, tags: List[str]) -> bool:
        """Append *tags* to the tag list of an existing episode.

        Existing tags are preserved; duplicates are silently deduplicated.

        Args:
            episode_id: UUID string of the target episode.
            tags:       List of tag strings to append.

        Returns:
            ``True`` if the episode was found and updated, ``False`` otherwise.
        """
        if not tags:
            return False
        with self._conn() as con:
            row = con.execute("SELECT tags FROM episodes WHERE id = ?", (episode_id,)).fetchone()
            if row is None:
                return False
            existing: List[str] = [t for t in (row["tags"] or "").split(",") if t]
            merged = list(dict.fromkeys(existing + tags))  # deduplicate, preserve order
            con.execute(
                "UPDATE episodes SET tags = ? WHERE id = ?",
                (",".join(merged), episode_id),
            )
        return True

    # ── Read ──────────────────────────────────────────────────────────────────

    def query_recent(
        self,
        limit: int = 20,
        action_type: Optional[str] = None,
        source: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Dict]:
        """Return the most recent episodes as a list of dicts.

        Args:
            limit:       Max number of records to return (capped at 500).
            action_type: If set, filter by ``action.type`` (e.g. ``"move"``).
            source:      If set, filter by origin (``"loop"``, ``"api"``,
                         ``"whatsapp"``, etc.).
            tags:        If set, only return episodes that contain ALL of the
                         specified tags (case-insensitive substring match on the
                         comma-separated ``tags`` column).
        """
        limit = min(max(1, limit), 500)
        where_clauses = []
        params: list = []

        if action_type:
            where_clauses.append("action_json LIKE ?")
            params.append(f'%"type": "{action_type}"%')

        if source:
            where_clauses.append("source = ?")
            params.append(source)

        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        params.append(limit)

        with self._conn() as con:
            rows = con.execute(
                f"SELECT * FROM episodes {where} ORDER BY ts DESC LIMIT ?",
                params,
            ).fetchall()

        results = [self._row_to_dict(r) for r in rows]

        if tags:
            filter_tags = [t.lower() for t in tags]
            results = [
                r
                for r in results
                if all(any(ft in stored.lower() for stored in r["tags"]) for ft in filter_tags)
            ]

        return results

    def get_episode(self, ep_id: str) -> Optional[Dict]:
        """Return a single episode by ID, or None if not found."""
        with self._conn() as con:
            row = con.execute("SELECT * FROM episodes WHERE id = ?", (ep_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def get_episode_image(self, episode_id: int) -> Optional[bytes]:
        """Return the stored JPEG thumbnail bytes for *episode_id*, or ``None``.

        Args:
            episode_id: The integer or UUID string of the episode row.

        Returns:
            Raw JPEG bytes when an image was stored, otherwise ``None``.
        """
        with self._conn() as con:
            row = con.execute(
                "SELECT image_blob FROM episodes WHERE id = ?", (episode_id,)
            ).fetchone()
        if row is None:
            return None
        return row["image_blob"]

    def episodes_with_images(self, limit: int = 20) -> List[Dict]:
        """Return recent episodes that have a stored image thumbnail.

        Each dict contains ``{id, ts, instruction, action_type, has_image}``
        but does *not* include the raw blob bytes (use
        :meth:`get_episode_image` to fetch those).

        Args:
            limit: Maximum number of episodes to return (capped at 500).

        Returns:
            List of dicts ordered newest-first.
        """
        limit = min(max(1, limit), 500)
        with self._conn() as con:
            rows = con.execute(
                """
                SELECT id, ts, instruction, action_json
                FROM   episodes
                WHERE  image_blob IS NOT NULL
                ORDER  BY ts DESC
                LIMIT  ?
                """,
                (limit,),
            ).fetchall()

        result: List[Dict] = []
        for row in rows:
            action_type = ""
            if row["action_json"]:
                try:
                    action_type = json.loads(row["action_json"]).get("type", "")
                except Exception:
                    pass
            result.append(
                {
                    "id": row["id"],
                    "ts": row["ts"],
                    "instruction": row["instruction"],
                    "action_type": action_type,
                    "has_image": True,
                }
            )
        return result

    def search(self, query: str, limit: int = 20) -> List[Dict]:
        """Full-text keyword search across instruction, raw_thought, and action_json.

        Uses SQLite LIKE for broad compatibility (no extra dependencies).
        Results are ordered newest-first.

        Args:
            query: Keyword or phrase to search for.  Case-insensitive.
                   Leading/trailing whitespace is stripped.
            limit: Maximum number of results (capped at 500).

        Returns:
            List of episode dicts (same format as :meth:`query_recent`).
        """
        query = (query or "").strip()
        if not query:
            return []
        limit = min(max(1, limit), 500)
        pattern = f"%{query}%"
        with self._conn() as con:
            rows = con.execute(
                """
                SELECT * FROM episodes
                WHERE  instruction  LIKE ?
                    OR raw_thought  LIKE ?
                    OR action_json  LIKE ?
                ORDER  BY ts DESC
                LIMIT  ?
                """,
                (pattern, pattern, pattern, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self) -> int:
        """Return the total number of stored episodes."""
        with self._conn() as con:
            return con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]

    # ── Export ────────────────────────────────────────────────────────────────

    def export_jsonl(self, path: str, limit: int = 0) -> int:
        """Write episodes to a JSON-Lines file.

        Args:
            path:  Output file path.
            limit: Max episodes to export; 0 means all.

        Returns:
            Number of episodes written.
        """
        sql = "SELECT * FROM episodes ORDER BY ts ASC"
        params: tuple = ()
        if limit > 0:
            sql += " LIMIT ?"
            params = (limit,)

        written = 0
        with self._conn() as con:
            rows = con.execute(sql, params).fetchall()
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(self._row_to_dict(row)) + "\n")
                written += 1
        return written

    # ── Admin ─────────────────────────────────────────────────────────────────

    def clear(self) -> int:
        """Delete ALL episodes.  Returns count deleted."""
        with self._conn() as con:
            n = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            con.execute("DELETE FROM episodes")
        return n

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        if d.get("action_json"):
            try:
                d["action"] = json.loads(d["action_json"])
            except Exception:
                d["action"] = None
        else:
            d["action"] = None
        del d["action_json"]
        # Replace raw blob with a boolean flag so JSON export stays clean.
        d["has_image"] = d.pop("image_blob", None) is not None
        # Convert comma-separated tags string to a list; empty list when NULL/empty.
        raw_tags: Optional[str] = d.get("tags")
        d["tags"] = [t for t in raw_tags.split(",") if t] if raw_tags else []
        return d

    @staticmethod
    def hash_image(image_bytes: bytes) -> str:
        """Return a short SHA-256 hex digest for a camera frame."""
        if not image_bytes:
            return ""
        return hashlib.sha256(image_bytes).hexdigest()[:16]
