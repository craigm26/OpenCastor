# scripts/image — the flashable Pi image

The operator's runbook is **[../../docs/IMAGE.md](../../docs/IMAGE.md)**. Read
that one. This file is the map of what is in here.

```
build-wheelhouse.sh        rootless. Every wheel the image needs, and a
                           scratch-venv proof that the set is complete.
build.sh                   the one sudo command. --dry-run first, always.
selftest.sh                rootless. Everything provable without root.
requirements-image.txt     the four packages the image's venv contains.

lib/common.sh              logging, asserts, byte math, fingerprints.
                           Sourced, not run.
lib/chroot-stage.sh        runs INSIDE the mounted image, under `unshare -n`:
                           accounts + venv. Asserts its own empty netns first.

firstboot/firstboot.sh              provisions once, degrades honestly.
firstboot/qr_server.py              the pairing page on :80. Stdlib only.
firstboot/opencastor-firstboot.service
firstboot/opencastor-qr.service
firstboot/ollama.service
```

Three commands, in order:

```bash
./scripts/image/build-wheelhouse.sh     # no sudo
./scripts/image/selftest.sh             # no sudo
sudo ./scripts/image/build.sh --dry-run # no sudo needed either, despite the sudo
sudo ./scripts/image/build.sh           # the real one
```
