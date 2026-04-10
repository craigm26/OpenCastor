# Competitive Positioning — Technical Due Diligence Reference

**Audience:** Engineering teams, system integrators, and compliance teams doing vendor evaluation.  
**Status:** Internal reference — not a marketing document.  
**Last updated:** 2026-04-10  
**Related design spec:** `docs/superpowers/specs/2026-04-10-opencastor-competitive-positioning-design.md`

This document compares the RCAN/OpenCastor safety architecture against the category of bolt-on LLM safety layers. It is honest about domain boundaries, genuine capability gaps, and open work. Claims that cannot be verified against a spec section or benchmark are not made.

---

## Domain Boundary

**RCAN/OpenCastor scope:** Physical robot actuation — systems where incorrect decisions cause physical harm. Safety constraints are structural at the protocol layer; they govern what commands can be dispatched to actuators, under what conditions, and with what human oversight.

**Bolt-on LLM safety layer scope:** Text inference — systems where a model generates text that a human or downstream system then acts on. Safety is applied as a post-generation filter: responses are scored for factual grounding, safety, or policy compliance before delivery.

These are different problems in different domains. The comparison below focuses on the intersection: regulated AI deployments requiring EU AI Act Article 12/14 compliance, audit trails, and human oversight records.

---

## Capability Comparison

| Capability | RCAN / OpenCastor | Bolt-on text safety layer |
|---|---|---|
| **Safety enforcement point** | Protocol layer — before dispatch to actuator | Post-generation filter — after model output |
| **Can AI agent bypass safety gate?** | No — structural transport constraint | Depends on implementation |
| **Audit chain** | HMAC-SHA256 append-only, ML-DSA-65 signed, chained hashes | HMAC audit logs (implementation-dependent) |
| **Post-quantum cryptography** | ML-DSA-65 (NIST FIPS 204) on all message signatures | HMAC-SHA256 (classical only) |
| **Human-in-the-loop** | §16.3 protocol-enforced gate (PENDING_AUTH → AUTHORIZE) | Application-layer oversight queue |
| **Multi-robot delegation** | `delegation_chain` with per-hop ML-DSA-65 signatures, max 4 hops | N/A — single inference endpoint |
| **Physical presence verification** | §PHYSICAL_PRESENCE message type | N/A — no physical embodiment |
| **Operational memory** | Structured YAML schema, confidence decay, EU AI Act Art. 13/17 alignment | N/A |
| **Open specification** | CC BY 4.0 — verifiable against spec + conformance suite | Proprietary |
| **Conformance levels** | L1–L4 with published test suite | Proprietary certification |
| **FRIA generation** | `castor fria generate` — signed JSON artifact ([#858](https://github.com/craigm26/OpenCastor/issues/858), in progress) | Claimed workflow (proprietary) |
| **EU AI Act Art. 50 watermarking** | §16.5 watermark token ([#857](https://github.com/craigm26/OpenCastor/issues/857), in progress) | HMAC watermark claimed |
| **Published performance metrics** | Safety benchmarks in progress ([#859](https://github.com/craigm26/OpenCastor/issues/859)) | AUROC 0.96 (hallucination, text inference) |
| **Deployment** | Open source, self-hosted, air-gap capable | Single Docker container, proprietary |

---

## Genuine Gaps (Open Issues)

These are real gaps relative to what bolt-on safety layer marketing claims. Each has a tracking issue.

### 1. AI Output Watermarking (EU AI Act Art. 50)

Bolt-on claim: HMAC watermark tokens in 6 languages.  
RCAN status: HMAC audit chains exist; per-output watermark token on AI-generated COMMAND messages is not yet implemented.  
Tracking: continuonai/rcan-spec#194 (spec), craigm26/OpenCastor#857 (implementation)

### 2. Automated FRIA Generation

Bolt-on claim: FRIA workflow with signed PDF/JSON export.  
RCAN status: Art. 9 template exists (`docs/compliance/art9-risk-assessment-template.md`); automated signed FRIA artifact generation from conformance output is not yet implemented.  
Tracking: continuonai/rcan-spec#195 (spec), craigm26/OpenCastor#858 (implementation)

### 3. Published Safety Metrics

Bolt-on claim: AUROC 0.96 (hallucination detection), <35ms total latency.  
RCAN status: No equivalent published numbers for confidence gate rejection rates or safety subsystem latency on production hardware.  
Note: AUROC is a text inference metric. The relevant metrics for physical robot safety gating are different (gate rejection rate at configured thresholds, HiTL latency, audit write latency). These are being measured on Raspberry Pi 5 + Hailo-8.  
Tracking: craigm26/OpenCastor#859

---

## What RCAN Claims That Can Be Verified Right Now

| Claim | Verify with |
|---|---|
| Confidence gates configured | `castor validate --category safety --json` → `safety.confidence_gates_configured: pass` |
| Local safety wins (structural protocol invariant) | `castor validate --category safety --json` → `safety.local_safety_wins: pass` |
| Protocol 66 safety rules conformant | `castor validate --category safety --json` → `safety.p66_conformance: pass` |
| Audit chain is tamper-evident | `castor audit verify` → chain integrity: OK |
| Message signing active (RCAN v1.5+) | `castor validate --category rcan_v15 --json` → `rcan_v15.message_signing: pass` |
| L2 conformance (all safety-critical checks) | `castor validate --strict` → exit code 0 |

---

## How to Respond to "Why not just use [bolt-on layer]?"

The short answer: bolt-on layers and RCAN solve different problems. If the question is "how do I make my text LLM safer," a bolt-on layer is a reasonable tool. If the question is "how do I deploy a physical robot in a regulated environment with auditable safety controls," the safety constraint needs to be at the protocol layer — not because of marketing positioning, but because of the physical harm model.

The longer answer is on [rcan.dev/safety](https://rcan.dev/safety).

---

## References

- [rcan.dev/safety](https://rcan.dev/safety) — Physical AI Safety Architecture
- [rcan.dev/compliance/frameworks](https://rcan.dev/compliance/frameworks) — Regulatory Coverage Index
- `docs/safety-architecture.md` — OpenCastor implementation reference
- `docs/compliance/eu-ai-act-mapping.md` — EU AI Act article-level mapping with conformity assessment citation guidance
- Design spec: `docs/superpowers/specs/2026-04-10-opencastor-competitive-positioning-design.md`
