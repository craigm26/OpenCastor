# EmbeddingInterpreter — Semantic Perception for Robots

The `EmbeddingInterpreter` gives OpenCastor robots a semantic memory layer. Instead of
treating every camera frame as raw pixels, the robot builds a persistent vector store of
past scenes and can recognize "I've seen something like this before" — without any API key.

---

## What It Does

1. **Encodes** each camera frame (and optionally audio) as an embedding vector
2. **Stores** the vector + context in an episode store at `~/.opencastor/episodes/`
3. **Retrieves** the top-k most similar past episodes at query time
4. **Injects** retrieved context into `TieredBrain.think()` via pre/post hooks (RAG)

The robot's LLM prompt now includes lines like:

```
[Memory] 3 similar scenes found:
- "obstacle on left, turned right, success" (similarity: 0.92)
- "narrow corridor, slowed to 30%, success" (similarity: 0.87)
- "blocked path, requested help via Telegram, success" (similarity: 0.81)
```

This lets the robot apply learned strategies to new-but-familiar situations without
retraining any model.

---

## Backend Tiers

| Tier | Backend | Dims | Cost | Requires |
|------|---------|------|------|---------|
| **Tier 0** | CLIP / SigLIP2 | 512 | Free | `pip install opencastor[clip]` |
| **Tier 1** | ImageBind / CLAP | 1024 | Free | `pip install opencastor[imagebind]` |
| **Tier 2** | Gemini Embedding 2 | 3072 (MRL) | Pay-per-use | `GOOGLE_API_KEY` set |

Auto-tier selection walks from Tier 0 → Tier 1 → Tier 2 → mock. Install only what you need:

```bash
pip install opencastor[clip]       # CLIP (recommended default)
pip install opencastor[imagebind]  # ImageBind + CLAP (experimental)
pip install "opencastor==3.*"     # Gemini Embedding 2 (uses existing google-genai dep)
```

### Tier 0 — CLIP / SigLIP2 (default)

- Model: `openai/clip-vit-base-patch32` (CLIP) or `google/siglip2-base-patch16-224` (SigLIP2)
- Dimensions: 512
- Hardware: CPU-only, runs on Raspberry Pi 4B+
- License: MIT (CLIP) / Apache 2.0 (SigLIP2)
- Latency: ~80ms on RPi5, ~12ms on M2 Mac

CLIP encodes images and text into the same 512-dim space. You can query "is there an
obstacle in front?" in text and retrieve visually similar episodes.

### Tier 1 — ImageBind / CLAP (experimental)

- ImageBind: `facebookresearch/ImageBind`, 1024-dim, 6 modalities (RGB/depth/audio/text/IMU/thermal)
- CLAP: `laion/clap-htsat-unfused`, audio↔text embeddings
- License: CC BY-NC 4.0 (ImageBind), MIT (CLAP)
- Note: ImageBind is non-commercial; check your use case

```bash
pip install opencastor[imagebind]
# Then follow docs/setup/imagebind-setup.md for model weights
```

### Tier 2 — Gemini Embedding 2

- Model: `gemini-embedding-2-preview`
- Dimensions: 3072 (full MRL), also 1536 or 768 (truncated)
- Accepts: PNG, JPEG, WAV (magic-byte detected), plain text
- Cost: ~$0.00002/request (Gemini API)
- Requires: `GOOGLE_API_KEY` in `.env`

---

## RCAN Configuration

The `interpreter:` block is optional. Omitting it uses `backend: auto`.

```yaml
# Minimal (auto-selects best available tier)
interpreter:
  backend: auto

# Explicit CLIP
interpreter:
  backend: clip

# Gemini Embedding 2 with custom dimensions
interpreter:
  backend: gemini
  gemini:
    dimensions: 1536          # 3072 | 1536 | 768
    task_type: retrieval_document

# Disable semantic memory entirely
interpreter:
  backend: mock
```

Valid `backend` values: `auto`, `clip`, `siglip2`, `imagebind`, `clap`, `gemini`, `mock`.

---

## Episode Store

Episodes are stored at `~/.opencastor/episodes/` as JSON lines + FAISS index (when available)
or numpy flat file (fallback).

```
~/.opencastor/episodes/
├── index.faiss        # FAISS flat L2 index (if faiss-cpu installed)
├── index.npy          # numpy fallback index
└── episodes.jsonl     # episode metadata
```

Each episode record:

```json
{
  "id": "ep_20260310_143022_a1b2",
  "ts": 1741614622.4,
  "embedding": [0.021, -0.143, ...],
  "scene_text": "corridor, poor lighting, obstacle detected left",
  "action": "turn_right",
  "outcome": "success",
  "similarity_threshold": 0.85
}
```

Retrieve similar episodes via the API:

```bash
curl http://localhost:8000/api/interpreter/status
# {"backend": "clip", "episode_count": 412, "index_type": "faiss", "dims": 512}
```

---

## RAG Context Injection

The interpreter hooks into `TieredBrain.pre_think()` and `post_think()`:

**Pre-think hook** — before the LLM call, the current frame is encoded and top-k similar
episodes are retrieved. The retrieved context is prepended to the system prompt.

**Post-think hook** — after the LLM returns a `Thought`, the new episode is encoded and
stored in the vector store.

Injection format in the system prompt:

```
[EmbeddingInterpreter: 3 similar scenes, top similarity 0.91]
Past episode 1 (sim=0.91): scene="narrow gap 0.4m", action="wait", outcome="success"
Past episode 2 (sim=0.87): scene="corridor blocked", action="request_help", outcome="success"
Past episode 3 (sim=0.82): scene="obstacle left 0.3m", action="turn_right", outcome="success"
```

---

## Prometheus Metrics

All embedding operations emit Prometheus metrics:

| Metric | Labels | Description |
|--------|--------|-------------|
| `opencastor_embedding_encode_total` | `backend`, `modality` | Total encode calls |
| `opencastor_embedding_encode_errors_total` | `backend`, `error_type` | Encode errors |
| `opencastor_embedding_latency_ms` | `backend`, `modality` | Encode latency histogram |
| `opencastor_embedding_store_size` | `backend` | Episode store size (gauge) |
| `opencastor_embedding_rag_hits_total` | `backend` | RAG retrievals with results |

```bash
curl http://localhost:8000/api/metrics | grep opencastor_embedding
```

---

## Dashboard

The Streamlit dashboard includes an **Embedding** tab showing:

- Current backend (auto-detected tier)
- Episode count and index type
- Top-k RAG preview for the latest frame
- Backend switcher (restart required)
- Benchmark runner (compares encode latency across available tiers)

---

## Benchmark Suite

```bash
castor benchmark embeddings        # compare all installed backends
castor benchmark embeddings --tier 0  # CLIP only
```

Output:

```
Backend    Dims   Latency p50   Latency p95   Episodes/sec
clip        512      82ms          94ms          12.2
siglip2     512      91ms         108ms          11.0
gemini     3072     210ms         280ms           4.8 (API)
```

---

## Examples

### Python SDK

```python
from castor.providers.embedding_interpreter import EmbeddingInterpreter

interp = EmbeddingInterpreter(config={"backend": "auto"})

# Encode a frame
ctx = interp.encode(image_bytes=frame_bytes)
print(ctx.embedding.shape)   # (512,) for CLIP

# Retrieve similar episodes
similar = interp.query(image_bytes=frame_bytes, top_k=5)
for ep in similar:
    print(ep.action, ep.outcome, ep.similarity)
```

### Disable for specific robots

Set `backend: mock` in the RCAN config to skip embedding entirely (e.g., resource-constrained
Arduino-only setups).

---

## Related

- [Monitoring docs](monitoring.md) — Prometheus setup + Grafana dashboard
- [docs/setup/imagebind-setup.md](setup/imagebind-setup.md) — ImageBind model weights
- [docs/design/episode-store-schema.md](design/episode-store-schema.md) — episode store schema

---

*Part of the [OpenCastor](https://github.com/craigm26/OpenCastor) project — Apache 2.0*
