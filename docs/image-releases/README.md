# Published image builds

The build artifacts themselves are **not** kept on the build host — they live on the
GitHub release. This directory keeps only the provenance: which commit produced which
image, and the checksum to verify a download against.

| Release | Built | Commit | Model |
|---|---|---|---|
| [`image-v0.1.0`](https://github.com/craigm26/OpenCastor/releases/tag/image-v0.1.0) | 2026-08-17 | `d21ef99` (`feat/pair-attest-pub`) | qwen3.5:2b |

The `.xz` is split into two parts because a GitHub release asset is capped at 2 GB.
To reassemble and verify:

```bash
cat opencastor-pi.img.xz.part-00 opencastor-pi.img.xz.part-01 > opencastor-pi.img.xz
sha256sum -c image-v0.1.0.sha256
```

Verified 2026-09-16 before deleting the 15 GB local build tree: the two published
parts are byte-identical to the local ones, and reassemble to the recorded sha256.
