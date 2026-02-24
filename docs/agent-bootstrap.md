# Agent Bootstrap (Deterministic Flow)

Use this short runbook when an autonomous agent needs a repeatable, self-checking startup and control sequence.

## 1) Install

```bash
python -m pip install -e ".[all]"
```

Expected output pattern (match one):
- `Successfully installed ...`
- `Requirement already satisfied: ...`

## 2) Verify Runtime

```bash
castor --help
castor status
```

Expected output pattern:
- `usage: castor ...`
- status-like fields such as `provider`, `channel`, `ready`, or `ok`

## 3) Run in Simulate Mode

```bash
castor run --config robot.rcan.yaml --simulate
```

Expected output pattern:
- `simulate` / `simulation` / `mock driver`
- loop startup messages (for example `run`, `started`, `watchdog`, or periodic telemetry)

## 4) Query Status / Health

In a second terminal while simulate mode is running:

```bash
castor status
curl -fsS http://127.0.0.1:8000/health
```

Expected output pattern:
- status shows runtime readiness (`ready`, `ok`, or similar)
- health returns JSON including indicators like `status`, `uptime`, `driver`, or `channels`

## 5) Issue Command

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/command \
  -H 'Content-Type: application/json' \
  -d '{"instruction":"move forward slowly for 1 second"}'
```

Expected output pattern:
- JSON response containing `raw_text`, `action`, or an explicit `error` payload

## 6) Collect Logs

```bash
castor logs --tail 100
```

Expected output pattern:
- recent timestamps and runtime events
- entries reflecting the issued instruction and action path

## 7) Safe Stop

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/stop
# then stop the foreground run process with Ctrl+C
```

Expected output pattern:
- stop acknowledgement (`ok`, `stopped`, or safety message)
- process exits cleanly without traceback

## Deep References

- CLI command surface: [docs/claude/cli-reference.md](./claude/cli-reference.md)
- API endpoints + payload contracts: [docs/claude/api-reference.md](./claude/api-reference.md)
- Extended operational examples: [docs/recipes-cli.md](./recipes-cli.md)
