# OpenCastor OS Security Deployment Guide

This guide provides a practical deployment baseline for hardened OpenCastor images on edge robots.

## 1) Secure boot and verified kernel/initramfs

### UEFI platforms (x86_64 / ARM server)
1. Enroll your Platform Key (PK), Key Exchange Key (KEK), and db signing cert.
2. Sign the bootloader, kernel, and initramfs with your trusted key.
3. Configure the bootloader to reject unsigned kernels and unsigned initramfs.
4. Verify with:
   - `mokutil --sb-state`
   - firmware event log / PCR[7] consistency checks.

### Raspberry Pi / SBC equivalent path
For Pi-class boards without full UEFI secure boot chain:
1. Use EEPROM + signed `boot.img` workflow where available.
2. Lock boot-order and disable USB mass-storage boot in production.
3. Store public verification key in read-only boot partition.
4. Verify kernel + initramfs hashes in early userspace before switching root.

## 2) A/B updates with signed artifacts

Use immutable A/B rootfs partitions (`rootfs_a`, `rootfs_b`) and a shared data partition:

- Active slot boots read-only.
- Update is written to inactive slot.
- Signed manifest validates slot image + kernel + initramfs hashes.
- Bootloader flips slot only after successful verification + health check.

Generate a signed manifest using:

```bash
scripts/security/sign_ab_update.sh \
  --slot-a out/rootfs_a.img \
  --slot-b out/rootfs_b.img \
  --kernel out/Image \
  --initramfs out/initramfs.img \
  --out out/update-manifest.json \
  --key keys/update_signing.pem
```

Install the corresponding verification public key at `/etc/opencastor/update-trust.pub`.

## 3) TPM measured boot and attestation token handoff

If TPM is available:
1. Measure firmware, bootloader, kernel, and initramfs into PCRs.
2. Run a local attestation agent that validates PCR policy.
3. Expose compact JSON attestation claims at:
   - `/proc/attestation/opencastor.json` or
   - `/run/opencastor/attestation.json`
4. Optional: include a short-lived remote attestation token (`token`).

OpenCastor reads that payload at startup and publishes it into `/proc/safety`.

## 4) Runtime startup check integration (gateway + runtime)

At startup, OpenCastor now:
- reads attestation claims,
- writes normalized posture into `/proc/safety`,
- marks degraded mode when claims are incomplete.

Surfaces include:
- `/proc/safety` and `/proc/safety/attestation_status`
- `GET /api/status` → `security_posture`

You can point OpenCastor at a custom attestation file with:

```bash
export OPENCASTOR_ATTESTATION_PATH=/run/opencastor/attestation.json
```

## 5) Minimum viable secure profile (Raspberry Pi-class)

When full TPM chain is unavailable, use **minimum-viable** profile:

- Boot integrity:
  - Signed boot artifacts where hardware supports it.
  - Read-only boot partition after provisioning.
- Update integrity:
  - A/B partitioning with signed update manifest.
  - Rollback on failed health check.
- Runtime integrity:
  - Generate local attestation claims via `scripts/security/collect_attestation.sh`.
  - Pass optional cloud-issued token using `OPENCASTOR_ATTESTATION_TOKEN`.
- Policy:
  - Treat missing TPM measurement as degraded (not trusted-equal).
  - Restrict high-risk actions when `/proc/safety/mode` is `degraded`.

Example systemd pre-start step:

```ini
ExecStartPre=/opt/opencastor/scripts/security/collect_attestation.sh /run/opencastor/attestation.json
Environment=OPENCASTOR_ATTESTATION_PATH=/run/opencastor/attestation.json
```

This profile is intentionally lightweight and achievable on constrained SBC deployments.

## 6) Mandatory access control + seccomp profiles

OpenCastor ships deployable Linux MAC/seccomp artifacts under `deploy/security/`:

- `deploy/security/apparmor/opencastor-gateway` — gateway profile with constrained file paths, device nodes, and exec transitions.
- `deploy/security/apparmor/opencastor-driver` — stricter driver-worker profile with no network access.
- `deploy/security/seccomp/gateway-seccomp.json` — gateway syscall allow-list.
- `deploy/security/seccomp/driver-strict-seccomp.json` — tighter driver syscall allow-list.

Install and load profiles:

```bash
bash deploy/security/install_profiles.sh
```

When `castor daemon enable` is used with hardened profile (default), generated units now include:

- `AppArmorProfile=opencastor-gateway` (or `opencastor-driver` for worker units)
- `SystemCallFilter=...` seccomp hardening rules

To verify runtime posture:

```bash
castor doctor
```

The doctor report includes a `MAC/seccomp` line that confirms profile installation plus active AppArmor + seccomp mode for the running daemon.
