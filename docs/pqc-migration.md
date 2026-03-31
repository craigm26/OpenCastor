# PQC Robot Identity Migration Guide

**Profile**: `pqc-hybrid-v1` (Ed25519 + ML-DSA-65)
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
all robot-to-robot messages.  OpenCastor implements a **hybrid profile** that
combines classical Ed25519 (for today's verifiers) with ML-DSA-65 (for
quantum resistance), requiring both signatures to pass verification.

Fleet expansion gates on confirmed PQC identity because:

1. **A compromised fleet identity cannot be revoked post-Q-Day.** Once
   classical cryptography breaks, any RS256/ES256 identity is permanently
   impersonatable.
2. **The RRF registry (§21) requires ML-DSA public keys** for new robot
   registrations as of RCAN v2.2.
3. **Fleet orchestrators verify peer identity** via `/.well-known/rcan-node.json`
   before accepting COMMAND messages.

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

### Identity record format (public, no private key material)

```json
{
  "crypto_profile":    "pqc-hybrid-v1",
  "pqc_public_key":    "<base64url ML-DSA-65 public key, 1952 bytes>",
  "ed25519_public_key": "<base64url Ed25519 public key, 32 bytes>"
}
```

These fields appear in `GET /.well-known/rcan-node.json` and in
`REGISTRY_REGISTER` (RCAN §21) payloads.

---

## Bob Migration Steps (RRN-000000000001)

Bob (`rrn://craigm26/robot/opencastor-rpi5-hailo/bob-001`) is the reference
robot and must be migrated first.

### 1. Generate identity (first boot after upgrade)

The gateway auto-generates a keypair on startup if
`~/.opencastor/robot_identity.json` does not exist.  On first generation the
log line reads:

```
PQC robot identity created (pqc-hybrid-v1). Private key stored at
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

Expected fields in the response:

```json
{
  "crypto_profile":    "pqc-hybrid-v1",
  "pqc_public_key":    "...",
  "ed25519_public_key": "..."
}
```

### 4. Register with the RRF

The `REGISTRY_REGISTER` (§21) message now includes the ML-DSA-65 public key in
the `public_key` field.  This happens automatically via `rcan.NodeClient` at
gateway startup when `rcan_protocol.registry` is set.

Verify registration:

```bash
castor registry status
```

### 5. Test sign/verify

```python
from castor.crypto.pqc import load_or_generate_robot_keypair, sign_robot_message, verify_robot_message

kp, _ = load_or_generate_robot_keypair()
msg = b"fleet:test:ping"
sig = sign_robot_message(kp, msg)
assert verify_robot_message(kp.ed25519_public, kp.ml_dsa_public, msg, sig)
print("PQC identity verified OK")
```

---

## Alex Migration Steps

Alex is a second robot scheduled for deployment.  **Alex must complete
migration before joining the fleet.**

1. Deploy OpenCastor ≥ v2026.3.31.0 on Alex's hardware.
2. Start the gateway once to auto-generate `~/.opencastor/robot_identity.json`.
3. Back up the private key (see Bob step 2).
4. Obtain an RRN from the RRF for Alex:
   ```bash
   castor registry request-rrn --robot-name alex --model opencastor-rpi5-hailo
   ```
5. Add Alex's RRN to `bob.rcan.yaml` under `fleet.robots` **after** verifying
   Alex's `/.well-known/rcan-node.json` returns valid PQC keys.
6. Run the conformance suite against Alex:
   ```bash
   pytest tests/ -k "conformance"
   ```

---

## Fleet Expansion Gate

**A robot MUST NOT be added to `fleet.robots` unless:**

- [ ] `GET /.well-known/rcan-node.json` returns `crypto_profile: "pqc-hybrid-v1"`
- [ ] `pqc_public_key` is present and non-empty (1952 bytes when decoded)
- [ ] `ed25519_public_key` is present and non-empty (32 bytes when decoded)
- [ ] Private key backup confirmed (offline storage)
- [ ] `pytest tests/test_pqc.py` passes on the robot

These checks prevent classical-only robots from entering a PQC-secured fleet
where their identity could be forged post-Q-Day.

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `OPENCASTOR_ROBOT_IDENTITY_PATH` | `~/.opencastor/robot_identity.json` | Override keypair file location |

---

## See Also

- [NIST FIPS 204 — ML-DSA](https://csrc.nist.gov/pubs/fips/204/final)
- [RCAN v2.2 spec](https://rcan.dev/spec/)
- [castor/crypto/pqc.py](../castor/crypto/pqc.py) — implementation
- [tests/test_pqc.py](../tests/test_pqc.py) — test suite
