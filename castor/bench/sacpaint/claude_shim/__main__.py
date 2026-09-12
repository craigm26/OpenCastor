"""CLI entry point: ``sacpaint-claude-shim`` / ``castor bench sacpaint shim``."""

from __future__ import annotations

import argparse
import logging
import sys

from castor.bench.sacpaint.claude_shim.server import DEFAULT_MAX_CONCURRENT_CHILDREN, serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sacpaint-claude-shim",
        description=(
            "Serve an OpenAI Chat Completions (and Anthropic Messages) endpoint on "
            "localhost that answers each request with one `claude -p` call, so a "
            "Claude subscription stands in for a metered API key. Results carry the "
            "claude-code-cli wire label and are not comparable to raw-API runs. "
            "Every POST must carry the run's bearer token: set SACPAINT_SHIM_TOKEN "
            "before starting, or use the token this prints on startup."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8931, help="bind port (default: 8931)")
    parser.add_argument(
        "--model",
        default="haiku",
        help="default CLI model alias or id when the request names none (default: haiku)",
    )
    parser.add_argument(
        "--claude-bin",
        default="claude",
        help="path to the claude CLI (use the full path if PATH is odd)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=6,
        help="CLI turn budget per request; images need a few for the Read tool (default: 6)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="seconds to wait for one CLI call (default: 300)",
    )
    parser.add_argument(
        "--max-concurrent-children",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_CHILDREN,
        help=(
            "hard ceiling on `claude -p` children in flight; requests over it get a 429 "
            f"rather than queueing (default: {DEFAULT_MAX_CONCURRENT_CHILDREN})"
        ),
    )
    parser.add_argument(
        "--keep-frames",
        action="store_true",
        help="keep the per-request camera frames on disk for debugging",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="logging verbosity (default: INFO)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    serve(
        host=args.host,
        port=args.port,
        model=args.model,
        claude_bin=args.claude_bin,
        max_turns=args.max_turns,
        timeout_s=args.timeout,
        keep_frames=args.keep_frames,
        max_concurrent_children=args.max_concurrent_children,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
