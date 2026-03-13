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
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.Memory")

_DEFAULT_DB_DIR = Path.home() / ".castor"
_DEFAULT_DB_NAME = "memory.db"


def _probe_pyarrow() -> bool:
    """Return True if pyarrow is importable, False otherwise."""
    try:
        import pyarrow  # noqa: F401

        return True
    except ImportError:
        return False


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
        CREATE TABLE IF NOT EXISTS episode_embeddings (
            id             TEXT PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
            embedding_json TEXT NOT NULL
        );
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
            # Migration: add reward_score + flagged for episode feedback (#262).
            try:
                con.execute("ALTER TABLE episodes ADD COLUMN reward_score REAL DEFAULT 0.0")
            except Exception:
                pass  # Column already exists
            try:
                con.execute("ALTER TABLE episodes ADD COLUMN flagged INTEGER DEFAULT 0")
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
        action: Optional[dict] = None,
        latency_ms: float = 0.0,
        image_hash: str = "",
        outcome: str = "ok",
        source: str = "loop",
        image_bytes: Optional[bytes] = None,
        tags: Optional[list[str]] = None,
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
        # Opportunistically store a semantic embedding (silent failure)
        try:
            text = f"{instruction} {raw_thought}".strip()
            emb = self._embed_text(text)
            if emb is not None:
                emb_json = json.dumps(emb)
                with self._conn() as con:
                    con.execute(
                        "INSERT OR REPLACE INTO episode_embeddings (id, embedding_json) VALUES (?, ?)",
                        (ep_id, emb_json),
                    )
        except Exception:
            pass
        return ep_id

    # ── Semantic search helpers (#301) ────────────────────────────────────────

    @staticmethod
    def _embed_text(text: str) -> Optional[list[float]]:
        """Return a unit-normalised embedding for *text*, or ``None`` if ST unavailable."""
        if not text.strip():
            return None
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]

            model_name = os.getenv("CASTOR_MEMORY_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
            model = SentenceTransformer(model_name)
            vec = model.encode(text, normalize_embeddings=True).tolist()
            return vec
        except Exception:
            return None

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        """Return cosine similarity between two pre-normalised vectors."""
        if len(a) != len(b):
            return 0.0
        return sum(x * y for x, y in zip(a, b, strict=False))

    def _search_semantic(
        self, query: str, limit: int, tags: Optional[list[str]] = None
    ) -> list[dict]:
        """Return episodes whose stored embeddings are most similar to *query*.

        Falls back to keyword search when SentenceTransformers are unavailable
        or no embeddings are stored.
        """
        query_emb = self._embed_text(query)
        if query_emb is None:
            logger.debug("EpisodeMemory: ST unavailable — falling back to keyword search")
            return self.search(query, limit=limit, mode="keyword", tags=tags)

        try:
            with self._conn() as con:
                rows = con.execute(
                    "SELECT e.*, ee.embedding_json FROM episodes e "
                    "JOIN episode_embeddings ee ON e.id = ee.id"
                ).fetchall()
        except Exception as exc:
            logger.warning("EpisodeMemory semantic: DB read failed: %s", exc)
            return []

        if not rows:
            return []

        scored = []
        for row in rows:
            try:
                emb = json.loads(row["embedding_json"])
                score = self._cosine(query_emb, emb)
                scored.append((score, row))
            except Exception:
                continue

        scored.sort(key=lambda t: t[0], reverse=True)
        results = [self._row_to_dict(r) for _, r in scored[:limit]]
        if tags:
            filter_tags = [t.lower() for t in tags]
            results = [
                r
                for r in results
                if all(any(ft in stored.lower() for stored in r["tags"]) for ft in filter_tags)
            ]
        return results

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

    def add_tags(self, episode_id: str, tags: list[str]) -> bool:
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
            existing: list[str] = [t for t in (row["tags"] or "").split(",") if t]
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
        tags: Optional[list[str]] = None,
    ) -> list[dict]:
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

    def get_episode(self, ep_id: str) -> Optional[dict]:
        """Return a single episode by ID, or None if not found."""
        with self._conn() as con:
            row = con.execute("SELECT * FROM episodes WHERE id = ?", (ep_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def replay_episode(self, episode_id: str) -> Optional[dict]:
        """Return a single episode by ID, or None if not found.

        Semantically named alias for :meth:`get_episode` for use in replay workflows.
        """
        return self.get_episode(episode_id)

    def replay_similar(self, query: str, top_k: int = 5) -> list[dict]:
        """Return the *top_k* episodes most similar to *query*, each annotated
        with a ``similarity_score`` field (float 0–1, descending).

        Falls back to keyword search (``similarity_score=0.0``) when embeddings
        are unavailable or no embeddings have been stored yet.

        Args:
            query: Natural-language query string.
            top_k: Maximum episodes to return (default 5).
        """
        query = (query or "").strip()
        if not query:
            return []
        top_k = max(1, top_k)

        query_emb = self._embed_text(query)
        if query_emb is None:
            logger.debug("replay_similar: ST unavailable — falling back to keyword search")
            results = self.search(query, limit=top_k, mode="keyword")
            for r in results:
                r["similarity_score"] = 0.0
            return results

        try:
            with self._conn() as con:
                rows = con.execute(
                    "SELECT e.*, ee.embedding_json FROM episodes e "
                    "JOIN episode_embeddings ee ON e.id = ee.id"
                ).fetchall()
        except Exception as exc:
            logger.warning("replay_similar: DB read failed: %s", exc)
            return []

        if not rows:
            logger.debug("replay_similar: no embeddings stored — falling back to keyword search")
            results = self.search(query, limit=top_k, mode="keyword")
            for r in results:
                r["similarity_score"] = 0.0
            return results

        scored: list[tuple] = []
        for row in rows:
            try:
                emb = json.loads(row["embedding_json"])
                score = self._cosine(query_emb, emb)
                scored.append((score, row))
            except Exception:
                continue

        scored.sort(key=lambda t: t[0], reverse=True)
        out = []
        for score, row in scored[:top_k]:
            d = self._row_to_dict(row)
            d["similarity_score"] = round(float(score), 6)
            out.append(d)
        return out

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

    def episodes_with_images(self, limit: int = 20) -> list[dict]:
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

        result: list[dict] = []
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

    def search(
        self,
        query: str,
        limit: int = 20,
        mode: str = "keyword",
        tags: Optional[list[str]] = None,
    ) -> list[dict]:
        """Search episodes by keyword or semantic similarity.

        Args:
            query: Keyword or phrase to search for.  Case-insensitive for keyword
                   mode; encoded via SentenceTransformers for semantic mode.
                   Leading/trailing whitespace is stripped.
            limit: Maximum number of results (capped at 500).
            mode:  ``"keyword"`` (default) — SQL LIKE search across instruction,
                   raw_thought, and action_json.  ``"semantic"`` — cosine
                   similarity against stored embeddings; falls back to keyword
                   search when SentenceTransformers are unavailable.
            tags:  If set, only return episodes that contain ALL of the
                   specified tags (case-insensitive substring match).

        Returns:
            List of episode dicts (same format as :meth:`query_recent`).
        """
        query = (query or "").strip()
        if not query:
            return []
        limit = min(max(1, limit), 500)
        if mode == "semantic":
            return self._search_semantic(query, limit, tags=tags)
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
        results = [self._row_to_dict(r) for r in rows]
        if tags:
            filter_tags = [t.lower() for t in tags]
            results = [
                r
                for r in results
                if all(any(ft in stored.lower() for stored in r["tags"]) for ft in filter_tags)
            ]
        return results

    def count(self) -> int:
        """Return the total number of stored episodes."""
        with self._conn() as con:
            return con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]

    # ── Episode feedback (#262) ───────────────────────────────────────────────

    def rate_episode(self, episode_id: str, score: float) -> bool:
        """Set the reward score for an episode.

        Args:
            episode_id: UUID of the episode to rate.
            score:      Reward score (e.g. ``+1.0`` for 👍, ``-1.0`` for 👎).

        Returns:
            True if the episode was found and updated, False otherwise.
        """
        with self._conn() as con:
            cur = con.execute(
                "UPDATE episodes SET reward_score = ? WHERE id = ?",
                (float(score), episode_id),
            )
            return cur.rowcount > 0

    def flag_episode(self, episode_id: str) -> bool:
        """Mark an episode as flagged for review.

        Args:
            episode_id: UUID of the episode to flag.

        Returns:
            True if the episode was found and flagged, False otherwise.
        """
        with self._conn() as con:
            cur = con.execute(
                "UPDATE episodes SET flagged = 1 WHERE id = ?",
                (episode_id,),
            )
            return cur.rowcount > 0

    def query_flagged(self, limit: int = 100) -> list[dict]:
        """Return episodes that have been flagged for review.

        Args:
            limit: Maximum number of episodes to return (default 100).

        Returns:
            List of episode dicts ordered newest-first.
        """
        limit = min(max(1, limit), 10_000)
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM episodes WHERE flagged = 1 ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

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

    def export_parquet(self, path: str, limit: int = 0) -> int:
        """Export episodes as an Apache Parquet file via pyarrow.

        Columns: id, ts, instruction, raw_thought, action_type, latency_ms,
                 outcome, source, tags (comma-separated string).

        Args:
            path:  Output ``.parquet`` file path.
            limit: Max episodes to export; 0 means all.

        Returns:
            Number of episodes written.

        Raises:
            ImportError: When pyarrow is not installed.
        """
        if not _probe_pyarrow():
            raise ImportError("pyarrow required: pip install pyarrow")

        import pyarrow as pa
        import pyarrow.parquet as pq

        sql = "SELECT * FROM episodes ORDER BY ts ASC"
        params: tuple = ()
        if limit > 0:
            sql += " LIMIT ?"
            params = (limit,)

        with self._conn() as con:
            rows = con.execute(sql, params).fetchall()

        columns: dict[str, list] = {
            "id": [],
            "ts": [],
            "instruction": [],
            "raw_thought": [],
            "action_type": [],
            "latency_ms": [],
            "outcome": [],
            "source": [],
            "tags": [],
        }

        for row in rows:
            columns["id"].append(row["id"] or "")
            columns["ts"].append(float(row["ts"] or 0.0))
            columns["instruction"].append(row["instruction"] or "")
            columns["raw_thought"].append(row["raw_thought"] or "")
            # Extract action type from action_json
            action_type = ""
            if row["action_json"]:
                try:
                    action_type = json.loads(row["action_json"]).get("type", "") or ""
                except Exception:
                    pass
            columns["action_type"].append(action_type)
            columns["latency_ms"].append(float(row["latency_ms"] or 0.0))
            columns["outcome"].append(row["outcome"] or "")
            columns["source"].append(row["source"] or "")
            # Store tags as comma-separated string (NULL → empty string)
            columns["tags"].append(row["tags"] or "")

        table = pa.Table.from_pydict(columns)
        pq.write_table(table, path)
        return len(rows)

    # ── Admin ─────────────────────────────────────────────────────────────────

    def clear(self) -> int:
        """Delete ALL episodes.  Returns count deleted."""
        with self._conn() as con:
            n = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            con.execute("DELETE FROM episodes")
        return n

    # ── Delta export (issue #330) ──────────────────────────────────────────────

    def get_latest_episode_id(self) -> Optional[str]:
        """Return the ``id`` of the most recently inserted episode, or ``None``.

        Uses SQLite ``rowid`` ordering (insertion order) rather than ``ts`` so
        that even episodes logged in rapid succession within the same
        millisecond are ordered consistently.

        Returns:
            UUID string of the latest episode, or ``None`` when the store is
            empty.
        """
        with self._conn() as con:
            row = con.execute("SELECT id FROM episodes ORDER BY rowid DESC LIMIT 1").fetchone()
        return row["id"] if row else None

    def export_delta(self, since_id: Optional[str], path: str) -> int:
        """Export episodes inserted *after* ``since_id`` to a JSONL file.

        Implements a checkpoint-style delta sync suitable for incremental
        replication to a remote data store or for feeding a downstream
        analysis pipeline.

        POST /api/memory/delta — endpoint stub (to be wired in api.py by main
        thread).  The endpoint should accept ``since_id`` as a query parameter
        and stream-return the JSONL file, or return the episode count.

        Args:
            since_id: UUID string of the last-seen episode.  All episodes whose
                      SQLite ``rowid`` is strictly greater than the rowid of
                      this episode are included.  Pass ``None`` or ``""`` to
                      export *all* episodes (equivalent to a full export).
            path:     Output file path for the JSONL file.  Will be created or
                      overwritten.

        Returns:
            Number of episodes written to *path*.
        """
        written = 0
        with self._conn() as con:
            if since_id:
                # Resolve the rowid of the checkpoint episode.
                ref = con.execute("SELECT rowid FROM episodes WHERE id = ?", (since_id,)).fetchone()
                if ref is not None:
                    rows = con.execute(
                        "SELECT * FROM episodes WHERE rowid > ? ORDER BY rowid ASC",
                        (ref["rowid"],),
                    ).fetchall()
                else:
                    # Unknown since_id — treat as full export.
                    rows = con.execute("SELECT * FROM episodes ORDER BY rowid ASC").fetchall()
            else:
                rows = con.execute("SELECT * FROM episodes ORDER BY rowid ASC").fetchall()

        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(self._row_to_dict(row)) + "\n")
                written += 1
        return written

    # ── Auto-summarize (issue #339) ────────────────────────────────────────────

    def _init_summaries_table(self) -> None:
        """Create the ``summaries`` table if it does not exist."""
        ddl = """
        CREATE TABLE IF NOT EXISTS summaries (
            id       TEXT PRIMARY KEY,
            ts       REAL NOT NULL,
            limit_n  INTEGER NOT NULL,
            summary  TEXT NOT NULL
        );
        """
        with self._conn() as con:
            con.executescript(ddl)

    def summarize_batch(self, provider_think_fn: Any, limit: int = 100) -> str:
        """Summarize the last ``limit`` episodes using an LLM provider.

        Fetches recent episodes via :meth:`query_recent`, builds a compact
        text representation, calls the provider, and stores the result in the
        ``summaries`` table.

        If the DB is empty or no episodes are returned, returns an empty string
        without calling the provider.

        Args:
            provider_think_fn: Callable matching the signature
                ``(image_bytes: bytes, instruction: str) -> Thought``.
                Typically ``brain.think``.
            limit:             Maximum number of recent episodes to summarise.
                               Defaults to 100.  Uses the capped
                               :meth:`query_recent` semantics (max 500).

        Returns:
            The ``raw_text`` of the Thought returned by the provider, or an
            empty string when there are no episodes to summarise.
        """
        self._init_summaries_table()

        effective_limit = max(1, limit)
        episodes = self.query_recent(limit=effective_limit)
        if not episodes:
            return ""

        lines: list[str] = []
        for ep in episodes:
            action_type = ""
            if ep.get("action") and isinstance(ep["action"], dict):
                action_type = ep["action"].get("type", "")
            instruction = ep.get("instruction") or ""
            outcome = ep.get("outcome") or ""
            lines.append(f"[{action_type}] {instruction} ({outcome})")

        body = "\n".join(lines)
        prompt = (
            f"You are summarising the recent activity log of a robot.\n"
            f"Write a 2-3 sentence natural language summary of the following episodes:\n\n"
            f"{body}\n\n"
            f"Summary:"
        )

        thought = provider_think_fn(b"", prompt)
        summary_text: str = thought.raw_text if thought is not None else ""

        # Persist the summary.
        summary_id = str(uuid.uuid4())
        summary_ts = time.time()
        with self._conn() as con:
            con.execute(
                "INSERT INTO summaries (id, ts, limit_n, summary) VALUES (?, ?, ?, ?)",
                (summary_id, summary_ts, effective_limit, summary_text),
            )

        return summary_text

    def get_latest_summary(self) -> Optional[dict]:
        """Return the most recently stored summary row, or ``None``.

        Returns:
            Dict with keys ``id``, ``ts``, ``limit_n``, ``summary``, or
            ``None`` when the ``summaries`` table is empty or does not exist.
        """
        self._init_summaries_table()
        with self._conn() as con:
            row = con.execute(
                "SELECT id, ts, limit_n, summary FROM summaries ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
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

    # ── Issue #342: K-means episode clustering (stdlib only) ──────────────────

    @staticmethod
    def _kmeans_distance_sq(a: list[float], b: list[float]) -> float:
        """Return squared Euclidean distance between two float vectors."""
        return sum((x - y) ** 2 for x, y in zip(a, b, strict=False))

    @staticmethod
    def _kmeans_centroid(vectors: list[list[float]]) -> list[float]:
        """Compute the mean centroid of a list of float vectors."""
        if not vectors:
            return []
        n = len(vectors[0])
        totals = [0.0] * n
        for v in vectors:
            for i, x in enumerate(v):
                totals[i] += x
        return [t / len(vectors) for t in totals]

    # ------------------------------------------------------------------
    # Issue #367 — action-tag frequency histogram
    # ------------------------------------------------------------------

    def tag_frequency(self, window_s: float = 3600.0, top_k: int = 10) -> list[dict]:
        """Return a histogram of action-type tags in the recent episode window.

        Reads episodes stored within the last *window_s* seconds and counts
        the ``action.type`` field of each episode.  Returns a list of
        ``{tag, count, frequency}`` dicts sorted descending by count, limited
        to *top_k* entries.

        Args:
            window_s: Look-back window in seconds (default: 3 600 s = 1 hour).
            top_k:    Maximum number of tags to return (default: 10).

        Returns:
            List of ``{"tag": str, "count": int, "frequency": float}`` dicts.
            Returns ``[]`` if no episodes exist.  Never raises.
        """
        import time as _time

        try:
            cutoff = _time.time() - max(0.0, window_s)
            with self._conn() as con:
                rows = con.execute(
                    "SELECT action_json FROM episodes WHERE ts >= ?",
                    (cutoff,),
                ).fetchall()

            import json as _json

            counts: dict[str, int] = {}
            for (action_json,) in rows:
                try:
                    action = _json.loads(action_json) if action_json else {}
                    tag = str(action.get("type", "unknown"))
                except Exception:
                    tag = "unknown"
                counts[tag] = counts.get(tag, 0) + 1

            total = sum(counts.values()) or 1
            sorted_tags = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_k]
            return [
                {"tag": tag, "count": cnt, "frequency": round(cnt / total, 4)}
                for tag, cnt in sorted_tags
            ]
        except Exception as exc:
            logger.warning("EpisodeMemory.tag_frequency error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Issue #410 — export tag frequency as CSV
    # ------------------------------------------------------------------

    def export_tags_csv(
        self,
        path: str,
        window_s: float = 3600.0,
        top_k: int = 20,
    ) -> dict[str, Any]:
        """Export action-tag frequency histogram to a CSV file (Issue #410).

        Calls tag_frequency() and writes results to a CSV with columns:
        tag, count, rank.

        Returns:
            Dict with ``"path"`` (str), ``"rows_written"`` (int), ``"window_s"`` (float).
        """
        import csv as _csv

        try:
            tags = self.tag_frequency(window_s=window_s, top_k=top_k)
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = _csv.writer(fh)
                writer.writerow(["tag", "count", "rank"])
                for rank, entry in enumerate(tags, start=1):
                    writer.writerow([entry.get("tag", ""), entry.get("count", 0), rank])
            return {"path": path, "rows_written": len(tags), "window_s": window_s}
        except Exception as exc:
            logger.warning("EpisodeMemory.export_tags_csv error: %s", exc)
            return {"path": path, "rows_written": 0, "window_s": window_s}

    # ------------------------------------------------------------------
    # Issue #415 — retention policy: auto-expire old episodes
    # ------------------------------------------------------------------

    def retention_policy(
        self,
        max_age_s: Optional[float] = None,
        max_count: Optional[int] = None,
        keep_flagged: bool = True,
    ) -> dict[str, Any]:
        """Auto-expire old episodes according to retention rules (Issue #415).

        Applies rules in this order:
        1. Delete episodes older than max_age_s (if set)
        2. Delete episodes beyond max_count (oldest first, if set)

        keep_flagged=True (default) prevents deletion of flagged episodes.

        Returns:
            Dict with ``"deleted_by_age"`` (int), ``"deleted_by_count"`` (int),
            ``"total_deleted"`` (int), ``"remaining"`` (int).
        """
        import time as _time

        deleted_by_age = 0
        deleted_by_count = 0
        try:
            with self._conn() as con:
                # --- Rule 1: delete by age ---
                if max_age_s is not None:
                    cutoff = _time.time() - max(0.0, float(max_age_s))
                    try:
                        if keep_flagged:
                            cur = con.execute(
                                "DELETE FROM episodes WHERE ts < ? AND flagged = 0",
                                (cutoff,),
                            )
                        else:
                            cur = con.execute(
                                "DELETE FROM episodes WHERE ts < ?",
                                (cutoff,),
                            )
                        deleted_by_age = cur.rowcount
                    except Exception:
                        # flagged column may not exist on old DBs
                        cur = con.execute(
                            "DELETE FROM episodes WHERE ts < ?",
                            (cutoff,),
                        )
                        deleted_by_age = cur.rowcount

                # --- Rule 2: delete by count ---
                if max_count is not None:
                    max_count = max(0, int(max_count))
                    total_row = con.execute("SELECT COUNT(*) FROM episodes").fetchone()
                    total = total_row[0] if total_row else 0
                    excess = total - max_count
                    if excess > 0:
                        try:
                            if keep_flagged:
                                ids_rows = con.execute(
                                    "SELECT id FROM episodes WHERE flagged = 0 "
                                    "ORDER BY ts ASC LIMIT ?",
                                    (excess,),
                                ).fetchall()
                            else:
                                ids_rows = con.execute(
                                    "SELECT id FROM episodes ORDER BY ts ASC LIMIT ?",
                                    (excess,),
                                ).fetchall()
                        except Exception:
                            ids_rows = con.execute(
                                "SELECT id FROM episodes ORDER BY ts ASC LIMIT ?",
                                (excess,),
                            ).fetchall()
                        ids = [r[0] for r in ids_rows]
                        if ids:
                            placeholders = ",".join("?" * len(ids))
                            cur2 = con.execute(
                                f"DELETE FROM episodes WHERE id IN ({placeholders})",
                                ids,
                            )
                            deleted_by_count = cur2.rowcount

            remaining = self.count()
        except Exception as exc:
            logger.warning("EpisodeMemory.retention_policy error: %s", exc)
            return {
                "deleted_by_age": 0,
                "deleted_by_count": 0,
                "total_deleted": 0,
                "remaining": 0,
            }

        return {
            "deleted_by_age": deleted_by_age,
            "deleted_by_count": deleted_by_count,
            "total_deleted": deleted_by_age + deleted_by_count,
            "remaining": remaining,
        }

    # ------------------------------------------------------------------
    # Issue #401 — per-tag episode timeline bucketed over time
    # ------------------------------------------------------------------

    def tag_timeline(
        self,
        tag: str,
        bucket_s: float = 3600.0,
        window_s: float = 86400.0,
    ) -> list[dict[str, Any]]:
        """Return per-tag episode counts bucketed over a time window.

        Scans episodes within the last *window_s* seconds and divides them
        into equal-width time buckets of *bucket_s* seconds.  Each bucket
        records how many episodes carried the specified *tag*.

        Args:
            tag:      Tag string to count (matched against comma-separated
                      ``tags`` column values).
            bucket_s: Width of each time bucket in seconds (default 3 600 s).
            window_s: Total look-back window in seconds (default 86 400 s).

        Returns:
            List of ``{"bucket_start": float, "bucket_end": float, "count": int}``
            dicts, one per bucket (including zero-count buckets).  Always
            returns at least one bucket.  Never raises.
        """
        import math as _math
        import time as _time

        try:
            bucket_s = max(1.0, float(bucket_s))
            window_s = max(bucket_s, float(window_s))
            now = _time.time()
            cutoff = now - window_s

            # Build bucket boundaries
            n_buckets = max(1, int(_math.ceil(window_s / bucket_s)))
            buckets: list[dict[str, Any]] = []
            for i in range(n_buckets):
                bstart = cutoff + i * bucket_s
                bend = bstart + bucket_s
                buckets.append({"bucket_start": bstart, "bucket_end": bend, "count": 0})

            with self._conn() as con:
                rows = con.execute(
                    "SELECT ts, tags FROM episodes WHERE ts >= ? ORDER BY ts ASC",
                    (cutoff,),
                ).fetchall()

            for row in rows:
                ts_val = row["ts"]
                raw_tags: Optional[str] = row["tags"]
                if not raw_tags:
                    continue
                row_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
                if tag not in row_tags:
                    continue
                # Find bucket index
                idx = int((ts_val - cutoff) / bucket_s)
                if 0 <= idx < n_buckets:
                    buckets[idx]["count"] += 1

            return buckets
        except Exception as exc:
            logger.warning("EpisodeMemory.tag_timeline error: %s", exc)
            # Return a minimal single bucket on error
            try:
                import time as _t2

                now2 = _t2.time()
                cutoff2 = now2 - float(window_s)
                return [{"bucket_start": cutoff2, "bucket_end": now2, "count": 0}]
            except Exception:
                return [{"bucket_start": 0.0, "bucket_end": 0.0, "count": 0}]

    # ------------------------------------------------------------------
    # Issue #407 — find episodes by outcome
    # ------------------------------------------------------------------

    def find_by_outcome(
        self,
        outcome: str,
        limit: int = 50,
        exact: bool = False,
    ) -> list[dict[str, Any]]:
        """Return episodes whose outcome matches the given string.

        Args:
            outcome: Outcome string to search for.
            limit:   Maximum number of results (default 50).
            exact:   When ``True`` match the outcome exactly; when ``False``
                     (default) perform a case-sensitive ``LIKE %outcome%``
                     substring match.

        Returns:
            List of episode dicts (same format as :meth:`query_recent`),
            ordered by ``ts DESC``.  Returns ``[]`` when no match.
            Never raises.
        """
        try:
            limit = max(1, int(limit))
            with self._conn() as con:
                if exact:
                    rows = con.execute(
                        "SELECT * FROM episodes WHERE outcome = ? ORDER BY ts DESC LIMIT ?",
                        (outcome, limit),
                    ).fetchall()
                else:
                    rows = con.execute(
                        "SELECT * FROM episodes WHERE outcome LIKE ? ORDER BY ts DESC LIMIT ?",
                        (f"%{outcome}%", limit),
                    ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception as exc:
            logger.warning("EpisodeMemory.find_by_outcome error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Issue #426 — outcome timeline bucketing
    # ------------------------------------------------------------------

    def outcome_timeline(
        self,
        outcome: str,
        bucket_s: float = 3600.0,
        window_s: float = 86400.0,
    ) -> list[dict[str, Any]]:
        """Return per-outcome episode counts bucketed over a time window.

        Scans episodes within the last *window_s* seconds and divides them
        into equal-width time buckets of *bucket_s* seconds.  Each bucket
        records how many episodes whose ``outcome`` column contains *outcome*
        (case-sensitive ``LIKE %outcome%`` substring match).

        Args:
            outcome:  Outcome substring to match (``LIKE %outcome%``).
            bucket_s: Width of each time bucket in seconds (default 3 600 s).
            window_s: Total look-back window in seconds (default 86 400 s).

        Returns:
            List of ``{"bucket_start": float, "bucket_end": float, "count": int}``
            dicts, one per bucket (including zero-count buckets).  Always
            returns at least one bucket.  Never raises.
        """
        import math as _math
        import time as _time

        try:
            bucket_s = max(1.0, float(bucket_s))
            window_s = max(bucket_s, float(window_s))
            now = _time.time()
            cutoff = now - window_s

            # Build bucket boundaries
            n_buckets = max(1, int(_math.ceil(window_s / bucket_s)))
            buckets: list[dict[str, Any]] = []
            for i in range(n_buckets):
                bstart = cutoff + i * bucket_s
                bend = bstart + bucket_s
                buckets.append({"bucket_start": bstart, "bucket_end": bend, "count": 0})

            with self._conn() as con:
                rows = con.execute(
                    "SELECT ts FROM episodes WHERE ts >= ? AND outcome LIKE ? ORDER BY ts ASC",
                    (cutoff, f"%{outcome}%"),
                ).fetchall()

            for row in rows:
                ts_val = row["ts"]
                idx = int((ts_val - cutoff) / bucket_s)
                if 0 <= idx < n_buckets:
                    buckets[idx]["count"] += 1

            return buckets
        except Exception as exc:
            logger.warning("EpisodeMemory.outcome_timeline error: %s", exc)
            # Return a minimal single bucket on error
            try:
                import time as _t2

                now2 = _t2.time()
                cutoff2 = now2 - float(window_s)
                return [{"bucket_start": cutoff2, "bucket_end": now2, "count": 0}]
            except Exception:
                return [{"bucket_start": 0.0, "bucket_end": 0.0, "count": 0}]

    def export_csv(
        self,
        path: str,
        window_s: float = 86400.0,
        limit: int = 1000,
    ) -> dict:
        """Export recent episodes to a CSV file.

        Writes a header row followed by one row per episode.  Columns:
        ``id``, ``ts``, ``instruction``, ``raw_thought``, ``action_type``,
        ``latency_ms``, ``outcome``, ``source``, ``tags``.

        Args:
            path:     File path to write (created or overwritten).
            window_s: Look-back window in seconds (default 86 400 = 24 h).
            limit:    Maximum number of rows to write (default 1 000).

        Returns:
            ``{"path": str, "rows_written": int, "columns": list}`` on success.
            ``{"error": str}`` on failure.  Never raises.
        """
        import csv as _csv
        import time as _time

        _COLUMNS = [
            "id",
            "ts",
            "instruction",
            "raw_thought",
            "action_type",
            "latency_ms",
            "outcome",
            "source",
            "tags",
        ]

        try:
            cutoff = _time.time() - max(0.0, window_s)
            with self._conn() as con:
                rows = con.execute(
                    """
                    SELECT id, ts, instruction, raw_thought, action_json,
                           latency_ms, outcome, source, tags
                    FROM episodes
                    WHERE ts >= ?
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    (cutoff, limit),
                ).fetchall()

            import json as _json

            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = _csv.DictWriter(fh, fieldnames=_COLUMNS)
                writer.writeheader()
                for row in rows:
                    action_json = row[4]
                    try:
                        action_type = (
                            _json.loads(action_json).get("type", "") if action_json else ""
                        )
                    except Exception:
                        action_type = ""
                    raw_tags = row[8]
                    tags_str = raw_tags if raw_tags else ""
                    writer.writerow(
                        {
                            "id": row[0],
                            "ts": row[1],
                            "instruction": row[2] or "",
                            "raw_thought": row[3] or "",
                            "action_type": action_type,
                            "latency_ms": row[5] or 0.0,
                            "outcome": row[6] or "",
                            "source": row[7] or "",
                            "tags": tags_str,
                        }
                    )

            return {"path": path, "rows_written": len(rows), "columns": _COLUMNS}

        except Exception as exc:
            logger.warning("EpisodeMemory.export_csv error: %s", exc)
            return {"error": str(exc)}

    def cluster_episodes(
        self,
        n_clusters: int = 5,
        by: str = "action_type",
        limit: int = 500,
        max_iter: int = 100,
        random_seed: int = 42,
    ) -> dict[str, Any]:
        """Group episodes using k-means clustering over action-type frequency vectors.

        Implements k-means from scratch using stdlib only (no sklearn/numpy).
        Each episode is represented as a frequency vector over the known action
        types (``move``, ``stop``, ``wait``, ``grip``, ``nav_waypoint``, and
        ``other``).  The algorithm runs for at most ``max_iter`` iterations or
        until cluster assignments converge.

        Args:
            n_clusters: Number of clusters (k).  Clamped to the number of
                        distinct episodes when fewer are available.
            by:         Feature scheme.  Currently only ``"action_type"`` is
                        supported; others raise ``ValueError``.
            limit:      Maximum number of recent episodes to cluster.
            max_iter:   Maximum k-means iterations before stopping.
            random_seed: Seed for the centroid initialisation RNG.

        Returns:
            A dict with keys:

            * ``"labels"`` — list of cluster indices (int) aligned with
              ``episode_ids``.
            * ``"centroids"`` — list of *n_clusters* centroid vectors
              (each a list of floats).
            * ``"episode_ids"`` — ordered list of episode ID strings that
              were clustered.
            * ``"representative_ids"`` — dict mapping cluster index (str) to
              the episode ID closest to that cluster's centroid.
            * ``"n_clusters"`` — actual number of clusters used.
            * ``"n_episodes"`` — number of episodes clustered.
            * ``"action_types"`` — ordered list of action-type feature names.

        Raises:
            ValueError: When ``by`` is not ``"action_type"`` or when no
                        episodes exist to cluster.
        """
        if by != "action_type":
            raise ValueError(f"cluster_episodes: unsupported 'by' value {by!r}. Use 'action_type'")

        # Fetch recent episodes
        episodes = self.query_recent(limit=limit)
        if not episodes:
            raise ValueError("cluster_episodes: no episodes found to cluster")

        # Feature dimension: ordered action types
        action_types = ["move", "stop", "wait", "grip", "nav_waypoint", "other"]
        n_features = len(action_types)
        type_idx = {t: i for i, t in enumerate(action_types)}

        # Build feature vectors: count occurrences of each action type per episode
        vectors: list[list[float]] = []
        ep_ids: list[str] = []

        for ep in episodes:
            ep_ids.append(ep["id"])
            action = ep.get("action") or {}
            action_type = action.get("type", "other") if isinstance(action, dict) else "other"
            vec = [0.0] * n_features
            vec[type_idx.get(action_type, type_idx["other"])] = 1.0
            vectors.append(vec)

        # Clamp n_clusters to the number of episodes
        k = min(n_clusters, len(vectors))

        # Initialise centroids via k-means++ (distance-weighted seeding).
        # This guarantees diverse initial centroids, preventing degenerate
        # clusters where all seeds map to the same feature vector.
        import random as _random

        rng = _random.Random(random_seed)

        # Pick first centroid at random
        first_idx = rng.randint(0, len(vectors) - 1)
        centroids: list[list[float]] = [vectors[first_idx][:]]

        for _ in range(k - 1):
            # For each vector, compute its distance to the nearest existing centroid
            distances = [
                min(self._kmeans_distance_sq(vec, c) for c in centroids) for vec in vectors
            ]
            # Pick the point with the maximum distance (deterministic k-means++)
            next_idx = max(range(len(distances)), key=lambda i: distances[i])
            centroids.append(vectors[next_idx][:])

        labels = [0] * len(vectors)

        for _iteration in range(max_iter):
            # Assignment step: assign each vector to nearest centroid
            new_labels = []
            for vec in vectors:
                best_c = 0
                best_d = float("inf")
                for c_idx, centroid in enumerate(centroids):
                    d = self._kmeans_distance_sq(vec, centroid)
                    if d < best_d:
                        best_d = d
                        best_c = c_idx
                new_labels.append(best_c)

            # Check for convergence
            if new_labels == labels:
                break
            labels = new_labels

            # Update step: recompute centroids
            for c_idx in range(k):
                cluster_vecs = [vectors[i] for i, lbl in enumerate(labels) if lbl == c_idx]
                if cluster_vecs:
                    centroids[c_idx] = self._kmeans_centroid(cluster_vecs)
                else:
                    # Empty cluster: reinitialise to a random vector
                    centroids[c_idx] = vectors[rng.randint(0, len(vectors) - 1)][:]

        # Find representative episode per cluster (closest to centroid)
        representative_ids: dict[str, str] = {}
        for c_idx in range(k):
            cluster_members = [
                (i, ep_ids[i], vectors[i]) for i, lbl in enumerate(labels) if lbl == c_idx
            ]
            if cluster_members:
                centroid = centroids[c_idx]
                best = min(cluster_members, key=lambda m: self._kmeans_distance_sq(m[2], centroid))
                representative_ids[str(c_idx)] = best[1]

        return {
            "labels": labels,
            "centroids": centroids,
            "episode_ids": ep_ids,
            "representative_ids": representative_ids,
            "n_clusters": k,
            "n_episodes": len(vectors),
            "action_types": action_types,
        }
