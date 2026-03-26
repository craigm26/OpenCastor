# OpenCastor Harness Research — Public Artifacts

This folder contains public-facing outputs from the [opencastor-autoresearch](https://github.com/craigm26/opencastor-autoresearch) pipeline.

## Files

| File | Description |
|---|---|
| `champion.yaml` | Current winning harness config — OHB-1 score 0.6541, 21/30 tasks passed |
| `leaderboard.csv` | Demo leaderboard data (seeded, not real runs) — 6 tiers × 5 robots |

## Apply the champion config

```bash
# Via CLI (opt-in only — never auto-applied)
castor harness apply-champion

# Via app
# Settings → Contribute → "Apply to this robot"
```

## OHB-1 Benchmark

The benchmark that produces these scores is defined at:
- Spec: [docs.opencastor.com/research/ohb1-benchmark](https://docs.opencastor.com/research/ohb1-benchmark/)
- Implementation: [opencastor-autoresearch/harness_research/benchmark.py](https://github.com/craigm26/opencastor-autoresearch)
- Local model: `gemma3:1b` via Ollama — runs on any Pi, no API key required

## Safety guarantee

Champion configs **cannot** modify P66 (physical consent), ESTOP logic, or motor parameters.
The `apply-champion` endpoint strips these fields before writing. See [docs.opencastor.com/runtime/safety](https://docs.opencastor.com/runtime/safety/).
