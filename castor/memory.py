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
    def _embed_text(text: str) -> Optional[List[float]]:
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
    def _cosine(a: List[float], b: List[float]) -> float:
        """Return cosine similarity between two pre-normalised vectors."""
        if len(a) != len(b):
            return 0.0
        return sum(x * y for x, y in zip(a, b, strict=False))

    def _search_semantic(
        self, query: str, limit: int, tags: Optional[List[str]] = None
    ) -> List[Dict]:
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

    def search(
        self,
        query: str,
        limit: int = 20,
        mode: str = "keyword",
        tags: Optional[List[str]] = None,
    ) -> List[Dict]:
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

        columns: Dict[str, list] = {
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

        lines: List[str] = []
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

    def get_latest_summary(self) -> Optional[Dict]:
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

    # ── Issue #342: K-means episode clustering (stdlib only) ──────────────────

    @staticmethod
    def _kmeans_distance_sq(a: List[float], b: List[float]) -> float:
        """Return squared Euclidean distance between two float vectors."""
        return sum((x - y) ** 2 for x, y in zip(a, b, strict=False))

    @staticmethod
    def _kmeans_centroid(vectors: List[List[float]]) -> List[float]:
        """Compute the mean centroid of a list of float vectors."""
        if not vectors:
            return []
        n = len(vectors[0])
        totals = [0.0] * n
        for v in vectors:
            for i, x in enumerate(v):
                totals[i] += x
        return [t / len(vectors) for t in totals]

    def cluster_episodes(
        self,
        n_clusters: int = 5,
        by: str = "action_type",
        limit: int = 500,
        max_iter: int = 100,
        random_seed: int = 42,
    ) -> Dict[str, Any]:
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
        vectors: List[List[float]] = []
        ep_ids: List[str] = []

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
        centroids: List[List[float]] = [vectors[first_idx][:]]

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
        representative_ids: Dict[str, str] = {}
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
