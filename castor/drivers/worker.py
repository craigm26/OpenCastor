"""CLI entrypoint for isolated driver workers.

Currently a placeholder process wrapper for systemd unit generation.
Composite-driver isolation uses an in-process multiprocess launcher in
``castor.drivers.ipc`` for local deployments.
"""

from __future__ import annotations

import argparse
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenCastor isolated driver worker")
    parser.add_argument("--config", required=True)
    parser.add_argument("--driver-id", required=True)
    args = parser.parse_args()

    # Worker execution for external service mode is intentionally minimal for now;
    # generated service templates are useful for integrators that provide their
    # own isolated worker launcher.
    print(f"driver worker shim started: id={args.driver_id} config={args.config}")
    while True:
        time.sleep(60)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
