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
ollama pull qwen3:1.7b                  # the one staged input a fresh host lacks
./scripts/image/build-wheelhouse.sh     # no sudo
./scripts/image/selftest.sh             # no sudo
sudo ./scripts/image/build.sh --dry-run # no sudo needed either, despite the sudo
sudo ./scripts/image/build.sh --shrink  # the real one
```

## The release is ONE file

`image-v0.1.0` shipped as `.part-00` + `.part-01` with a `cat` and a
`sha256sum -c` in the release body. That is a terminal, on the one path whose
central claim is that there is no terminal, and the ten-minute stopwatch did
not count it because the stopwatch started at "click Write". Both are fixed:
the stopwatch now starts at the download link, and `build.sh` refuses to emit
an `.xz` larger than `--asset-budget` (default 2 GiB, GitHub's per-asset cap).
It checks twice — in preflight from measured inputs, so you find out at minute
zero, and against the real file before it says "done" — and neither check
offers `split` as a way out.

**The model is the release size.** Measured on the 2026-08-17 build: 3.32 GB
of `.xz`, of which 2.74 GB was `qwen3.5:2b`'s already-compressed blobs, leaving
580 MB for the rootfs, the venv and ollama. So the model's budget for a single
asset is 2 GiB − 580 MB = 1.57 GB, no amount of package-stripping closes a
1.17 GB gap out of a 580 MB pool, and the default model moved to `qwen3:1.7b`
(1.36 GB of blobs, 1.94 GB projected `.xz`, 198 MiB of margin). The full table
and the fallbacks are in [../../docs/IMAGE.md](../../docs/IMAGE.md#one-file-and-what-it-costs).

The publish, after a green build:

```bash
gh release create image-vX.Y.Z --repo craigm26/OpenCastor \
  --title "OpenCastor Pi image X.Y.Z" \
  ~/image-build/work/opencastor-pi.img.xz \
  ~/image-build/work/opencastor-pi.img.xz.sha256 \
  ~/image-build/work/opencastor-image.json
```

`build.sh` prints that command, paths filled in, when it finishes.
