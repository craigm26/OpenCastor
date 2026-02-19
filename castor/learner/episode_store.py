"""Thread-safe episode storage backed by JSON files."""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path
from typing import Optional

from .episode import Episode

DEFAULT_STORE_DIR = Path.home() / ".opencastor" / "episodes"


class EpisodeStore:
    """Persists episodes as JSON files with file-locking for thread safety."""

    def __init__(self, store_dir: Optional[Path] = None) -> None:
        self.store_dir = store_dir or DEFAULT_STORE_DIR
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, episode_id: str) -> Path:
        return self.store_dir / f"{episode_id}.json"

    def _write_locked(self, path: Path, data: dict) -> None:
        with open(path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _read_locked(self, path: Path) -> dict:
        with open(path) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def save(self, episode: Episode) -> None:
        """Save an episode to disk."""
        self._write_locked(self._path_for(episode.id), episode.to_dict())

    def load(self, episode_id: str) -> Episode:
        """Load an episode by ID. Raises FileNotFoundError if missing."""
        path = self._path_for(episode_id)
        data = self._read_locked(path)
        return Episode.from_dict(data)

    def list_recent(self, n: int = 10) -> list[Episode]:
        """Return the N most recent episodes sorted by start_time descending."""
        episodes = self._load_all()
        episodes.sort(key=lambda e: e.start_time, reverse=True)
        return episodes[:n]

    def list_by_outcome(self, success: bool = True) -> list[Episode]:
        """Return episodes filtered by success/failure."""
        return [e for e in self._load_all() if e.success == success]

    def delete(self, episode_id: str) -> None:
        """Delete an episode file."""
        path = self._path_for(episode_id)
        path.unlink(missing_ok=True)

    def cleanup(self, max_age_days: int = 30) -> int:
        """Remove episodes older than max_age_days. Returns count removed."""
        cutoff = time.time() - (max_age_days * 86400)
        removed = 0
        for path in self.store_dir.glob("*.json"):
            try:
                data = self._read_locked(path)
                if data.get("start_time", 0) < cutoff:
                    path.unlink()
                    removed += 1
            except (json.JSONDecodeError, OSError):
                continue
        return removed

    def _load_all(self) -> list[Episode]:
        episodes: list[Episode] = []
        for path in self.store_dir.glob("*.json"):
            try:
                data = self._read_locked(path)
                episodes.append(Episode.from_dict(data))
            except (json.JSONDecodeError, OSError):
                continue
        return episodes
