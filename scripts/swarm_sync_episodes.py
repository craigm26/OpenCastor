#!/usr/bin/env python3
"""swarm_sync_episodes.py — bidirectional episode sync between swarm nodes.

Pulls learned episodes from each node and POSTs them to the other so both
robots share the owner's interaction history and improve together.

Designed to run as a cron job every 5 minutes:
  */5 * * * * /home/craigm26/opencastor/venv/bin/python3 \
      /home/craigm26/OpenCastor/scripts/swarm_sync_episodes.py >> /tmp/swarm_sync.log 2>&1
"""

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [swarm-sync] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("swarm-sync")

NODES = [
    {
        "name": "alex",
        "url": "http://192.168.68.98:8000",
        "token": "ea3c155db3cdc1a3221a7ebfe683954d85924784a5e87a45accbd848c3497b4f",
    },
    {
        "name": "bob",
        "url": "http://192.168.68.61:8001",
        "token": "c0c700dddec89e27cd98cc53189620acad01032acc67a849",
    },
]

EPISODE_LIMIT = 50  # how many recent episodes to pull per node per run


def _req(method: str, url: str, token: str, body: dict | None = None) -> dict | list | None:
    data = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        log.warning("%s %s -> HTTP %s", method, url, e.code)
        return None
    except Exception as e:
        log.warning("%s %s -> %s", method, url, e)
        return None


def get_episodes(node: dict) -> list[dict]:
    result = _req("GET", f"{node['url']}/api/learner/episodes?limit={EPISODE_LIMIT}", node["token"])
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "episodes" in result:
        return result["episodes"]
    return []


def post_episode(node: dict, episode: dict) -> bool:
    result = _req("POST", f"{node['url']}/api/learner/episode", node["token"], episode)
    return result is not None


def sync(source: dict, target: dict) -> int:
    episodes = get_episodes(source)
    if not episodes:
        log.info("  %s: no episodes to sync", source["name"])
        return 0

    # Get target's existing episode IDs to avoid duplicates
    existing = get_episodes(target)
    existing_ids = {e.get("episode_id") or e.get("id") for e in existing if e}

    pushed = 0
    for ep in episodes:
        ep_id = ep.get("episode_id") or ep.get("id")
        if ep_id and ep_id in existing_ids:
            continue
        # Tag the episode with its origin so the target knows who learned it
        ep.setdefault("metadata", {})
        ep["metadata"]["synced_from"] = source["name"]
        ep["metadata"]["synced_at"] = datetime.utcnow().isoformat()
        if post_episode(target, ep):
            pushed += 1

    log.info("  %s -> %s: pushed %d new episodes", source["name"], target["name"], pushed)
    return pushed


def main() -> None:
    log.info("Starting episode sync — %d nodes", len(NODES))
    total = 0
    # Bidirectional: each node syncs to every other
    for i, source in enumerate(NODES):
        for j, target in enumerate(NODES):
            if i == j:
                continue
            total += sync(source, target)
    log.info("Sync complete — %d episodes exchanged", total)


if __name__ == "__main__":
    main()
