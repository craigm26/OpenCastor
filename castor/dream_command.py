"""`castor dream`: save, check and clear the Dream Labs key on this machine.

The key goes where Dream Labs' own tools read it (`castor.dream`), so a
Microduck bridge on this machine answers `dream.*` with it and the phone never
has to hold it.
"""

from __future__ import annotations

import getpass
import sys

from castor import dream


def _models(key: str, duck: bool) -> int:
    try:
        models = dream.list_models(key)
    except dream.DreamError as why:
        print(f"Dream Labs refused: {why}", file=sys.stderr)
        return 1
    fits = dream.MICRODUCK_EMBODIMENT if duck else None
    print(f"key {dream.hint(key)} works; {len(models)} model(s) it may run:")
    for m in models:
        s = dream.model_summary(m, fits)
        mark = "" if fits is None else ("  fits a Microduck" if s["fits"] else "  not a duck model")
        ready = "ready" if s["servable"] else "not ready"
        print(f"  {s['id']}  ({s['owner']}, {ready}, cameras {s['cameras']}){mark}")
    if duck and not any(dream.fits_embodiment(m, dream.MICRODUCK_EMBODIMENT) for m in models):
        print(
            "\nNo model here was trained on a duck. Dream-1's public model drives two-arm YAM robots;\n"
            "a duck runs a model fine-tuned on duck recordings (castor's MICRODUCK_EMBODIMENT), made on\n"
            "https://platform.dreamlabs.ai/fine-tuning."
        )
    return 0


def cmd_dream(args) -> int:
    action = getattr(args, "dream_cmd", None) or "status"
    if action == "key":
        key = getattr(args, "key", None)
        if not key:
            if not sys.stdin.isatty():
                key = sys.stdin.readline()
            else:
                key = getpass.getpass("Dream Labs API key (dl_live_...), not shown: ")
        try:
            key = dream.check_key_text(key)
        except ValueError as why:
            print(f"not saved: {why}", file=sys.stderr)
            return 1
        try:
            dream.list_models(key)
        except dream.DreamError as why:
            print(f"not saved, Dream Labs refused it: {why}", file=sys.stderr)
            return 1
        path = dream.save_key(key)
        print(f"saved key {dream.hint(key)} to {path} (0600)")
        return 0
    if action == "clear":
        print("cleared" if dream.clear_key() else f"no key line in {dream.credentials_path()}")
        return 0
    key = dream.load_key()
    if not key:
        print(f"no Dream Labs key here; `castor dream key` saves one to {dream.credentials_path()}")
        return 1 if action == "check" else 0
    if action == "check":
        return _models(key, duck=getattr(args, "duck", False))
    print(
        f"key {dream.hint(key)} ({'environment' if __import__('os').environ.get(dream.KEY_ENV) else dream.credentials_path()})"
    )
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser(
        "dream",
        help="Use your Dream Labs (Dream-1) API key on this robot",
        description=(
            "Dream-1 is Dream Labs' vision-language-action model. Save your own key here once;\n"
            "a Microduck bridge on this machine then answers the app's dream.* requests with it."
        ),
    )
    s = p.add_subparsers(dest="dream_cmd")
    k = s.add_parser("key", help="Check a key with Dream Labs, then save it (asks, not shown)")
    k.add_argument("--key", help="the key; omit to be asked, or pipe it on stdin")
    c = s.add_parser("check", help="List the models the saved key may run")
    c.add_argument("--duck", action="store_true", help="mark the models a Microduck can run")
    s.add_parser("clear", help="Remove the saved key")
    s.add_parser("status", help="Say whether a key is saved (default)")
