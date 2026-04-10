# Design: OpenCastor Ecosystem Competitive Positioning

**Date:** 2026-04-10  
**Scope:** craigm26/OpenCastor + continuonai/rcan-spec  
**Status:** Approved — pending implementation plan  
**Trigger:** Geodesia G-1 bot comment on continuonai/rcan-spec#191; gap analysis against bolt-on LLM safety layer category  

---

## 1. Problem Statement

The RCAN protocol and OpenCastor runtime implement a technically superior safety architecture for physical AI systems: post-quantum cryptography, hardware-bound robot identity, protocol-enforced HiTL gates, multi-robot delegation chains, and structured operational memory with EU AI Act alignment. None of this is currently surfaced in a form that developers doing vendor evaluation or compliance teams doing framework mapping can find or cite.

A category of competitor — exemplified by Geodesia G-1 — is actively marketing to the same regulatory audience (EU AI Act, NIST AI RMF) using published metrics, named framework counts, and automated GitHub outreach. Their product is a bolt-on hallucination scoring layer for text LLMs. The domain difference (text inference vs. physical robot actuation) is real and defensible, but only if the RCAN/OpenCastor ecosystem makes the case explicitly.

This design covers two parallel workstreams:

1. **Content**: new pages on rcan.dev and in OpenCastor docs that surface existing capabilities for both developer and compliance audiences
2. **Capability gaps**: three features that close genuine gaps against the competitor's claims, each tracked as GitHub issues in the appropriate repository

---

## 2. Audience

**Primary**: Developers and system integrators evaluating RCAN for robot deployments — they read spec pages, look for conformance levels, want code references.

**Secondary**: Compliance teams, CTOs, and CISOs at organizations deploying physical robots in regulated environments (EU AI Act Annex III scope) — they look for framework mappings, audit artifacts, and citable technical documentation.

Content must work for both: technically precise enough for developers to trust, structured enough for compliance teams to cite in technical files.

---

## 3. Content Architecture

### 3.1 rcan.dev — New Pages (continuonai/rcan-spec)

#### `/safety` — Physical AI Safety Architecture

The anchor page. Explains how RCAN enforces safety at the protocol layer for systems where incorrect decisions have physical consequences. Written as technical architecture documentation, not marketing copy.

**Structure:**
- H2: "Safety is a protocol constraint, not an application concern" — why enforcement at the protocol layer matters for physical systems
- H2: "Confidence gating (§16.2)" — how per-scope minimum confidence thresholds prevent command dispatch below defined floors; cite the threshold math
- H2: "Human-in-the-Loop gates (§16.3)" — structural enforcement: PENDING_AUTH → AUTHORIZE flow; cannot be bypassed by the AI agent; contrast with application-layer checks
- H2: "Tamper-evident audit chain" — HMAC-SHA256 append-only log; ML-DSA-65 signing; QuantumLink-Sim commitment chain; what "tamper-evident" means concretely (any modification breaks chain verification)
- H2: "Post-quantum identity (ML-DSA-65)" — why ML-DSA-65 over HMAC-SHA256; NIST PQC standardisation context; RRN binding
- H2: "Multi-robot delegation chain" — human provenance preserved across robot-to-robot command delegation; `delegation_chain` array; max 4 hops; audit record serialises full chain
- H2: "Physical presence verification (§PHYSICAL_PRESENCE)" — no text AI equivalent; what this means for regulated environments
- H2: "Operational memory with confidence decay (robot-memory.md)" — structured observational history with EU AI Act Art. 13/17 alignment
- Footer: links to `/compliance/frameworks`, OpenCastor safety-architecture.md, rcan-spec conformance page

**SEO target terms**: `physical AI safety protocol`, `robot EU AI Act compliance`, `autonomous robot audit trail`, `robot safety standards`, `ML-DSA robot signing`, `RCAN protocol safety`, `robot conformance levels`

**Tone**: Technical reference. No superlatives. Claims are backed by spec section numbers or benchmark results. Where a number doesn't yet exist (pending Gap 3 issue), say "benchmarks forthcoming — see OpenCastor#[issue]".

---

#### `/compliance/frameworks` — Regulatory Coverage Index

A single-source-of-truth table: every regulatory framework RCAN addresses, the specific RCAN provision mapped to each article or clause, and the coverage classification (Full / Partial / Out of scope). Sourced from the existing compliance docs in `rcan-spec/docs/compliance/`.

**Frameworks covered:**

| Framework | Key Articles/Clauses | RCAN Provisions | Coverage |
|---|---|---|---|
| EU AI Act (2024/1689) | Art. 9 (risk mgmt), Art. 12 (record-keeping), Art. 13 (transparency), Art. 14 (human oversight), Art. 17 (QMS), Art. 50 (watermarking — pending §16.5) | §7 ConfidenceGate, §8 HiTL, §16.1 AI block, §16.2–16.4, AuditChain, robot-memory.md | Full (technical layer) |
| NIST AI RMF 1.0 | GOVERN, MAP, MEASURE, MANAGE | RBAC (GOVERN), conformance levels (MEASURE), audit chain (MANAGE) | Substantial |
| ISO 10218-1:2025 | Safety requirements for industrial robots | Protocol 66 safety rules, geofencing, physical bounds, emergency stop | Partial |
| IEC 62443 | Industrial cybersecurity | Ed25519/ML-DSA-65 signing, RBAC, session timeouts, rate limiting | Partial |
| GDPR Art. 22 | Automated decision-making | HiTL gates, thought log, human oversight workflow | Partial |
| HIPAA | Medical device AI | Audit chain, role-gated data access, privacy-by-default sensor policy | Partial |
| ISO 42001 | AI management systems | Conformance levels L1–L4, quality management protocol controls | Partial |

Page includes methodology note: "coverage classifications refer to protocol-layer technical controls only; organizational, procedural, and regulatory obligations remain the responsibility of the provider/deployer."

**SEO target terms**: `robot EU AI Act mapping`, `RCAN compliance frameworks`, `ISO 10218 AI robotics`, `physical robot regulatory compliance`, `autonomous robot NIST AI RMF`

---

#### Homepage — "Why an open protocol beats a bolt-on layer"

A new section (3 paragraphs, no bullet lists) added to the rcan.dev homepage. Technically argued, not promotional in tone.

**Draft content direction:**
- Para 1: The fundamental problem with safety layers applied to a running model is that they sit outside the execution boundary — they observe outputs but cannot constrain what the model attempts. For text applications this is an acceptable trade-off. For physical robots, a command that clears the hallucination filter but executes against the wrong joint can cause harm before any post-hoc check runs. Protocol-level enforcement closes this gap: RCAN's HiTL gates and confidence thresholds are structural constraints, not filters.
- Para 2: Auditability is similarly structural in RCAN. The ML-DSA-65 signed audit chain records every command with principal identity, model confidence, thought provenance, and delegation path at dispatch time — not reconstructed afterward. This is the record-keeping architecture Art. 12 of the EU AI Act requires, built into the protocol rather than retrofitted.
- Para 3: RCAN is an open specification. Every claim in this documentation can be verified against the spec, the reference implementation, and the conformance test suite. Conformance levels L1–L4 define what "compliant" means concretely.

---

### 3.2 OpenCastor Docs — New Documents

#### `docs/safety-architecture.md`

Implementation companion to rcan.dev/safety. Where the rcan.dev page explains the protocol, this explains how OpenCastor instantiates it with code references.

**Structure:**
- Safety kernel overview and module map
- `castor/fs/safety.py` — virtual filesystem safety layer
- `castor/rcan/rbac.py` — RBAC with rate limiting and session timeouts
- `castor/brain/memory_schema.py` — confidence decay implementation
- `castor/brain/robot_context.py` — context injection and watermarking (post §16.5 implementation)
- `castor/brain/autodream.py` — nightly memory reinforcement loop
- Audit chain write path and integrity verification
- Conformance test references

#### `docs/compliance/competitive-positioning.md`

Internal technical reference for teams doing vendor evaluation. Honest comparison: what RCAN/OpenCastor does, what bolt-on text safety layers do, where the domain boundaries are, and which gaps are tracked as open issues.

Not published as marketing. Framed as engineering due-diligence documentation.

---

## 4. Capability Gaps → GitHub Issues

### Gap 1: AI Output Watermarking (EU AI Act Art. 50)

RCAN has HMAC-SHA256 audit chains but no per-output watermark token embedded in AI-generated command payloads. EU AI Act Art. 50 requires AI-generated content to be machine-detectable.

**rcan-spec issue**: `§16.5 — AI Output Watermarking`
- Spec a deterministic HMAC watermark token embedded in every AI-generated `COMMAND` message
- Token format: `rcan-wm-v1:{hmac_sha256(rrn + thought_id + timestamp, robot_signing_key)[:16]}`
- Required audit record field: `watermark_token`
- Verification endpoint: `GET /api/v1/watermark/verify?token=&rrn=`
- SDK surface in rcan-py and rcan-ts
- EU AI Act Art. 50 compliance note in spec text

**OpenCastor issue**: `Implement §16.5 AI output watermarking in brain and audit pipeline`
- Embed watermark token at command dispatch in `castor/brain/robot_context.py`
- Verify and record in audit chain
- Expose via existing API surface

### Gap 2: Automated FRIA Generation

Static Art. 9 template exists in `rcan-spec/docs/compliance/art9-risk-assessment-template.md`. No automated workflow produces a signed FRIA artifact.

**rcan-spec issue**: `§19 — Fundamental Rights Impact Assessment (FRIA) Protocol`
- Define FRIA JSON document schema
- Required fields: system identity (RRN), deployment classification (Annex III basis), risk entries sourced from conformance gaps, human oversight configuration, signing key reference
- Trigger conditions: L2+ conformance deployments
- Signing: ML-DSA-65 with robot's identity key
- Export format: JSON canonical + PDF rendering hint (for notified body submission)

**OpenCastor issue**: `castor fria generate — automated FRIA artifact from conformance output`
- New CLI command: `castor fria generate --rrn RRN-xxx --output fria.json`
- Sources: `ConformanceChecker.run_all()`, robot identity from config, `robot-memory.md` `hardware_observation` entries
- Signs output with robot key
- Produces JSON artifact citable in conformity assessment technical file

### Gap 3: Published Safety Metrics

No equivalent to Geodesia's AUROC/latency claims. Compliance buyers cannot compare on benchmarks.

**OpenCastor issue**: `Publish confidence gate and safety subsystem benchmarks`
- Extend `castor/benchmarker.py` with safety benchmark suite
- Metrics to capture:
  - Confidence gate rejection rate at configured thresholds (on a labelled inference test set)
  - HiTL gate round-trip latency: dispatch → PENDING_AUTH → AUTHORIZE → confirmed
  - Audit chain write latency per record (p50, p95, p99)
  - ML-DSA-65 signing overhead per command
- Output: `safety-benchmark.json` artifact + `docs/safety-benchmarks.md` with methodology
- These numbers become citable references on rcan.dev/safety

---

## 5. Issue #191 Response

Reply to the Geodesia bot comment (continuonai/rcan-spec#191) with a substantive technical clarification. Not a rebuttal — a precise framing of the domain difference.

**Draft direction:**
- Acknowledge Geodesia is addressing a real compliance need for text LLM deployments
- Note the domain difference: RCAN's scope is physical robot actuation — where the safety constraint must be structural at the protocol layer rather than a post-generation filter, because a command that passes a hallucination score but executes against the wrong actuator causes harm before any check can intervene
- Reference concrete RCAN provisions: §16.2 confidence gates, §16.3 HiTL gates, ML-DSA-65 audit chain, robot-memory.md Art. 13/17 alignment
- Note the FRIA and watermarking work being specced (link to the new issues)
- Invite collaboration if Geodesia's layer could sit above an RCAN-compliant runtime for text-generating robot components

---

## 6. Success Criteria

- rcan.dev/safety and /compliance/frameworks indexed and ranking for target terms within 60 days
- GitHub issues filed and linked from this spec: 2 in rcan-spec, 3 in OpenCastor
- Issue #191 reply posted
- `docs/safety-architecture.md` and `docs/compliance/competitive-positioning.md` committed to OpenCastor
- Safety benchmark results published as a follow-on once Gap 3 issue is implemented
- No unverified metric claims in any published content — every number links to a spec section, benchmark output, or open issue tracking the measurement

---

## 7. Out of Scope

- Modifying existing RCAN spec sections (§1–§18) beyond adding §16.5 and §19
- OpenCastor feature work beyond the three gap issues
- Any claim of superiority not backed by a spec reference or benchmark
- rcan-py or rcan-ts SDK changes beyond watermark token surface (tracked in their own repos if needed)
