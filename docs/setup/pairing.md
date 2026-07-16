# Pairing your robot with the OpenCastor iOS app

`castor pair` is the one command that connects the free OpenCastor iOS app to a
robot running the [robot-md-gateway](https://github.com/RobotRegistryFoundation/robot-md-gateway).
It prints a QR code you scan from the app, and — in the same step — generates the
gateway's Ed25519 **attestation identity** so every `/v1/invoke` decision comes
back as a signed, verifiable receipt.

## Prerequisites

1. A robot with a `ROBOT.md` manifest and the gateway installed:
   ```bash
   pip install "opencastor==3.*"          # the runtime (ships `castor pair`)
   pip install robot-md-gateway            # the enforcement gateway
   ```
2. Bearer tokens for the gateway. Generate them once with the gateway wizard:
   ```bash
   robot-md-gateway init          # writes bearers.yaml (+ .env) next to ROBOT.md
   ```

## Run `castor pair`

From the robot host:

```bash
castor pair \
  --manifest-path /home/pi/ROBOT.md \
  --bearers /home/pi/bearers.yaml \
  --gateway-url http://robot.local:8080
```

`castor pair`:

1. **Generates an Ed25519 attestation keypair** (throwaway PKCS8 PEM), writes the
   private key to `~/.config/opencastor/attestation/gateway-attestation.pem`
   (mode `0600`) and a public key alongside it.
2. **Wires the gateway config** — writes
   `ROBOT_MD_ATTESTATION_KEY_FILE` and `ROBOT_MD_ATTESTATION_KID` into
   `~/.config/opencastor/gateway-attestation.env`. These are the exact variables
   the gateway's attestation loader reads, so signed receipts turn on with no code
   change.
3. **Prints a scannable QR** encoding the pairing payload plus the decoded JSON.

### The pairing QR payload

```json
{
  "v": 1,
  "gateway_url": "http://robot.local:8080",
  "bearer": "actuate-token-abc",
  "manifest_path": "/home/pi/ROBOT.md",
  "rrn": "RRN-000000000011",
  "estop_url": "http://robot.local:8001/api/stop"
}
```

`manifest_path` **must** ride in the QR: it is a gateway-host-local filesystem
path that every `InvokeEnvelope` requires and the phone cannot guess. `estop_url`
is optional and only present if you pass `--estop-url`.

## Start the gateway with attestation enabled

`castor pair` prints these lines — run them to (re)start the gateway with the
attestation identity it just created:

```bash
set -a; . ~/.config/opencastor/gateway-attestation.env; set +a
robot-md-gateway serve \
  --robot-md /home/pi/ROBOT.md \
  --bearers /home/pi/bearers.yaml \
  --host 0.0.0.0 --port 8080
```

Now scan the QR from the app's **Set Up** screen. The first `/v1/invoke` returns a
signed receipt (`envelope_signature: {kid, alg, sig}`) the app verifies offline.

## Notes

- The install command is pinned (`opencastor==3.*`). A bare `pip install opencastor`
  can resolve a stale CalVer release — see [pypi-versioning](../pypi-versioning.md).
- Re-running `castor pair` refuses to overwrite an existing key unless you pass
  `--force` (which rotates the attestation identity).
