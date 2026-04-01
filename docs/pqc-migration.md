# PQC Robot Identity Migration Guide

**Profiles**: `pqc-v1` (owned robots) and `pqc-hybrid-v1` (external registrations)
**Issue**: #808
**Status**: Active — required before fleet expansion

---

## Why PQC Now

Classical signing algorithms (RSA, ECDSA/ES256, RS256) are vulnerable to
Shor's algorithm on a sufficiently large quantum computer.  NIST's 2022
post-quantum standardization process selected **ML-DSA (CRYSTALS-Dilithium)**
as the primary lattice-based digital signature scheme, published as
[FIPS 204](https://csrc.nist.gov/pubs/fips/204/final) in August 2024.

The RCAN v2.2 specification mandates ML-DSA-65 as the signing algorithm for
all robot-to-robot messages.  OpenCastor supports two profiles — choose based
on whether the robot is operator-owned or externally registered.

Fleet expansion gates on confirmed PQC identity because:

1. **A compromised fleet identity cannot be revoked post-Q-Day.** Once
   classical cryptography breaks, any RS256/ES256 identity is permanently
   impersonatable.
2. **The RRF registry (§21) requires ML-DSA public keys** for new robot
   registrations as of RCAN v2.2.
3. **Fleet orchestrators verify peer identity** via `/.well-known/rcan-node.json`
   before accepting COMMAND messages.

---

## Two Profiles: Which to Use

| | `pqc-v1` | `pqc-hybrid-v1` |
|---|---|---|
| **Use for** | Owned/internal robots (Bob, Alex, operator-controlled) | External/third-party robot registrations |
| **Algorithms** | ML-DSA-65 only | Ed25519 + ML-DSA-65 |
| **Verification** | ML-DSA-65 must pass | Both Ed25519 AND ML-DSA-65 must pass |
| **Ed25519 key in identity record** | No | Yes |
| **`ROBOT_OWNER_MODE`** | `true` (default) | `false` |

**Rule of thumb:** If you control both ends of the communication (operator owns
the robot and the verifier), use `pqc-v1`.  If external third parties may
verify the robot's identity using classical Ed25519, use `pqc-hybrid-v1`.

---

## Profile Definition: `pqc-v1`

| Field | Value |
|---|---|
| `crypto_profile` | `"pqc-v1"` |
| PQC algorithm | ML-DSA-65 FIPS 204 (1952-byte public key, 3309-byte signatures) |
| Signature format | `"pqc-v1.<base64url ML-DSA-65 signature>"` |
| Verification rule | ML-DSA-65 must pass |
| Key storage | `~/.opencastor/robot_identity.json` (JSON, base64url) |
| Public endpoint | `GET /.well-known/rcan-node.json` |

### Signature format

```
pqc-v1.<base64url ML-DSA-65 signature>
```

### Identity record format

```json
{
  "crypto_profile": "pqc-v1",
  "pqc_public_key": "<base64url ML-DSA-65 public key, 1952 bytes>"
}
```

Note: `ed25519_public_key` is intentionally absent for `pqc-v1` robots.

---

## Profile Definition: `pqc-hybrid-v1`

| Field | Value |
|---|---|
| `crypto_profile` | `"pqc-hybrid-v1"` |
| Classical algorithm | Ed25519 (32-byte keys, 64-byte signatures) |
| PQC algorithm | ML-DSA-65 FIPS 204 (1952-byte public key, 3309-byte signatures) |
| Signature envelope | base64url(JSON `{profile, ed25519, ml_dsa_65}`) |
| Verification rule | **Both** Ed25519 AND ML-DSA-65 must pass |
| Key storage | `~/.opencastor/robot_identity.json` (JSON, base64url) |
| Public endpoint | `GET /.well-known/rcan-node.json` |

### Signature envelope format

```json
{
  "profile": "pqc-hybrid-v1",
  "ed25519":  "<base64url Ed25519 signature>",
  "ml_dsa_65": "<base64url ML-DSA-65 signature>"
}
```

This envelope is itself base64url-encoded and returned as a string by
`castor.crypto.pqc.sign_robot_message()`.

### Identity record format

```json
{
  "crypto_profile":    "pqc-hybrid-v1",
  "pqc_public_key":    "<base64url ML-DSA-65 public key, 1952 bytes>",
  "ed25519_public_key": "<base64url Ed25519 public key, 32 bytes>"
}
```

---

## Configuration

### Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `ROBOT_OWNER_MODE` | `true` | `true` → `pqc-v1` (owned robot), `false` → `pqc-hybrid-v1` (external) |
| `OPENCASTOR_ROBOT_IDENTITY_PATH` | `~/.opencastor/robot_identity.json` | Override keypair file location |

### Setting in `.env`

```bash
# Owned/internal robot (default — Bob, Alex, operator hardware)
ROBOT_OWNER_MODE=true

# External/third-party registration
ROBOT_OWNER_MODE=false
```

---

## Bob Migration Steps (RRN-000000000001)

Bob (`rrn://craigm26/robot/opencastor-rpi5-hailo/bob-001`) is the reference
robot.  Bob is operator-owned → uses `pqc-v1`.

### 1. Generate identity (first boot after upgrade)

The gateway auto-generates a `pqc-v1` keypair on startup if
`~/.opencastor/robot_identity.json` does not exist (when `ROBOT_OWNER_MODE=true`,
which is the default).  On first generation the log reads:

```
PQC robot identity created (pqc-v1). Private key stored at
~/.opencastor/robot_identity.json — back up before fleet expansion.
```

### 2. Back up the private key

```bash
# On Bob (robot.local / 192.168.68.61)
cp ~/.opencastor/robot_identity.json /media/usb/bob_robot_identity_backup.json
chmod 600 /media/usb/bob_robot_identity_backup.json
```

Store the backup offline (USB, password manager, or encrypted vault).
**Do not commit to git or push to any remote.**

### 3. Verify the identity endpoint

```bash
curl -s http://robot.local:8000/.well-known/rcan-node.json | python3 -m json.tool
```

Expected fields for pqc-v1:

```json
{
  "crypto_profile": "pqc-v1",
  "pqc_public_key": "..."
}
```

Note: `ed25519_public_key` is intentionally absent for owned robots.

### 4. Register with the RRF

The `REGISTRY_REGISTER` (§21) message includes the ML-DSA-65 public key in
the `public_key` field.  This happens automatically via `rcan.NodeClient` at
gateway startup when `rcan_protocol.registry` is set.

```bash
castor registry status
```

### 5. Test sign/verify

```python
from castor.crypto.pqc import (
    load_or_generate_robot_keypair, PQC_V1,
    sign_robot_message_v1, verify_robot_message_v1,
)

kp, _ = load_or_generate_robot_keypair(profile=PQC_V1)
msg = b"fleet:test:ping"
sig = sign_robot_message_v1(kp.ml_dsa_private, msg)
assert verify_robot_message_v1(kp.ml_dsa_public, msg, sig)
print("PQC pqc-v1 identity verified OK")
```

---

## Alex Migration Steps

Alex is operator-owned → uses `pqc-v1`.

1. Deploy OpenCastor ≥ v2026.3.31.0 on Alex's hardware.
2. Ensure `ROBOT_OWNER_MODE=true` (the default) in Alex's `.env`.
3. Start the gateway once to auto-generate `~/.opencastor/robot_identity.json`.
4. Back up the private key (see Bob step 2).
5. Obtain an RRN from the RRF for Alex:
   ```bash
   castor registry request-rrn --robot-name alex --model opencastor-rpi5-hailo
   ```
6. Add Alex's RRN to `bob.rcan.yaml` under `fleet.robots` **after** verifying
   Alex's `/.well-known/rcan-node.json` returns `crypto_profile: "pqc-v1"`.
7. Run the conformance suite against Alex:
   ```bash
   pytest tests/ -k "conformance"
   ```

---

## External Robot Registration (pqc-hybrid-v1)

For robots not owned by the operator (third-party integrations, partner robots):

1. Set `ROBOT_OWNER_MODE=false` on the external robot.
2. The gateway generates a `pqc-hybrid-v1` keypair (Ed25519 + ML-DSA-65).
3. `/.well-known/rcan-node.json` exposes both `pqc_public_key` and `ed25519_public_key`.
4. Classical verifiers can use Ed25519; PQC verifiers use ML-DSA-65.

---

## Fleet Expansion Gate

**A robot MUST NOT be added to `fleet.robots` unless:**

For pqc-v1 (owned robots):
- [ ] `GET /.well-known/rcan-node.json` returns `crypto_profile: "pqc-v1"`
- [ ] `pqc_public_key` is present and non-empty (1952 bytes when decoded)
- [ ] Private key backup confirmed (offline storage)
- [ ] `pytest tests/test_pqc.py` passes on the robot

For pqc-hybrid-v1 (external robots):
- [ ] `GET /.well-known/rcan-node.json` returns `crypto_profile: "pqc-hybrid-v1"`
- [ ] `pqc_public_key` is present and non-empty (1952 bytes when decoded)
- [ ] `ed25519_public_key` is present and non-empty (32 bytes when decoded)
- [ ] Private key backup confirmed (offline storage)
- [ ] `pytest tests/test_pqc.py` passes on the robot

---

## See Also

- [NIST FIPS 204 — ML-DSA](https://csrc.nist.gov/pubs/fips/204/final)
- [RCAN v2.2 spec](https://rcan.dev/spec/)
- [castor/crypto/pqc.py](../castor/crypto/pqc.py) — implementation
- [tests/test_pqc.py](../tests/test_pqc.py) — test suite
