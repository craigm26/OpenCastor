# Design: EU AI Act Compliance Study — RCAN Ecosystem

**Date:** 2026-04-11
**Status:** Approved — pending implementation plan
**Scope:** rcan-spec, OpenCastor, Robot Registry Foundation (RRF)
**Deadline:** August 2, 2026 — Art. 9, 12–17 obligations apply to high-risk AI systems

---

## 1. Problem Statement

OpenCastor and the RCAN specification implement numerous EU AI Act-relevant features (FRIA generation, watermarking, HiTL gates, audit trail, SBOM, ML-DSA-65 signing), but no single authoritative document maps EU AI Act articles to RCAN provisions and OpenCastor/RRF implementations. Without this mapping:

- Gaps may be missed before the August 2, 2026 enforcement deadline
- Notified body reviewers have no structured evidence package to assess
- Future compliance changes have no baseline to diff against

This study produces: (1) an article-by-article compliance matrix across all three repos, (2) a prioritized gap list, and (3) implementation tasks for every Critical or Warn gap.

---

## 2. Approach

**Audit-first, fix-second.** The compliance matrix is written before any implementation work begins. Only gaps rated Critical or Warn produce implementation tasks. Info items are documented but not actioned.

Data flows in one direction: EU AI Act article text → RCAN provision → implementation evidence → status rating → gap detail → implementation task (if needed).

---

## 3. Scope

### Repositories

| Repo | Role |
|---|---|
| `rcan-spec` (`/home/craigm26/rcan-spec`) | Protocol specification — must mandate required behaviors |
| `OpenCastor` (`/home/craigm26/OpenCastor`) | Reference implementation — must implement what spec requires |
| Robot Registry Foundation (RRF) | Registry service — must support Art. 49 database registration and Art. 16(j) authority access |

### Articles Covered

| Article | Topic |
|---|---|
| Art. 9 | Risk management system |
| Art. 10 | Data and data governance |
| Art. 11 | Technical documentation |
| Art. 12 | Record-keeping / audit logs |
| Art. 13 | Transparency and instructions for use |
| Art. 14 | Human oversight measures |
| Art. 15 | Accuracy, robustness, cybersecurity |
| Art. 16 | Provider obligations |
| Art. 17 | Quality management system |
| Art. 43 | Conformity assessment |
| Art. 49 | EU AI Act database registration |
| Art. 50 | Transparency / AI output detectability |
| Art. 72 | Post-market monitoring |
| Annex III | High-risk classification basis |

**Not covered** (purely organizational deployer/operator obligations with no protocol or code expression): Art. 18–22, 23–25.

---

## 4. Matrix Structure

Each article entry follows this template:

```
## Art. N — [Title]

| Dimension       | Detail |
|-----------------|--------|
| Requirement     | Plain-language summary of what the article requires |
| RCAN Provision  | Spec section(s) that mandate compliance |
| OpenCastor      | File/function that implements it |
| RRF             | Registry service coverage (if applicable) |
| Status          | ✅ Pass / ⚠️ Warn / ❌ Gap |
| Gap Detail      | What is missing or incomplete |
| Priority        | Critical / Warn / Info |
| Deadline Risk   | High (before Aug 2, 2026) / Low |
```

### Gap Severity Rules

| Rating | Meaning |
|---|---|
| **Critical** | No implementation anywhere; blocks compliance; must be fixed |
| **Warn** | Implementation exists but incomplete, undocumented, or has a known weakness |
| **Info** | Implementation complete; noted for auditor context; no action needed |

Only Critical and Warn gaps produce implementation tasks.

---

## 5. Compliance Matrix

### Art. 9 — Risk Management System

| Dimension | Detail |
|---|---|
| Requirement | Providers must establish, implement, document, and maintain a risk management system throughout the AI system lifecycle. Must identify known and foreseeable risks, estimate and evaluate risks, adopt risk mitigation measures. |
| RCAN Provision | §6 Safety Invariants (audit trail, prompt injection defense); §8 HiTL gates; `p66_manifest.py` capability/constraint declarations |
| OpenCastor | `castor/safety/protocol.py` (SafetyLayer), `castor/safety/bounds.py` (BoundsChecker), `castor/safety/p66_manifest.py` (P66Manifest), `castor/safety/anti_subversion.py` (prompt injection defense), `castor/safety_benchmark.py` (quantified latency evidence) |
| RRF | P66 manifest exposed at `GET /api/safety/manifest`; risks declared in RRN metadata |
| Status | ✅ Pass |
| Gap Detail | None. Protocol-enforced risk controls documented in P66 manifest with machine-readable status (implemented/partial/planned). Safety benchmarks (#859) provide quantified evidence. |
| Priority | Info |
| Deadline Risk | Low |

---

### Art. 10 — Data and Data Governance

| Dimension | Detail |
|---|---|
| Requirement | Training, validation, and testing data must meet quality criteria. Providers must document data governance practices, examine for possible biases. |
| RCAN Provision | No dedicated section. OpenCastor uses pre-trained external models (Gemini, Claude, GPT, Ollama) — training data governance is provider-side (Google, Anthropic, OpenAI, etc.). |
| OpenCastor | `castor/providers/` — adapters to external providers. No training pipeline exists in-repo. |
| RRF | Not applicable — RRF is a registry, not a model trainer. |
| Status | ⚠️ Warn |
| Gap Detail | OpenCastor does not train models, so Art. 10 obligations fall primarily on the upstream AI providers (Anthropic, Google, OpenAI). However, no documentation exists stating this explicitly, and no mechanism exists to declare which model version was used at deploy time (model pinning). If a deployer uses a non-certified model, the chain of responsibility is unclear. |
| Priority | Warn |
| Deadline Risk | High |

**Gap: Add model provenance field to RCAN config and FRIA document.** When `castor fria generate` runs, the model name + version from the active provider config should be embedded in the FRIA under a `model_provenance` block, documenting the upstream AI provider responsible for Art. 10 obligations.

---

### Art. 11 — Technical Documentation

| Dimension | Detail |
|---|---|
| Requirement | Providers must draw up technical documentation before placing a high-risk AI system on the market. Documentation must include system description, design specifications, development process, validation results, and monitoring measures. |
| RCAN Provision | §22 FRIA (references `rcan.dev/spec/section-22`); rcan-spec `docs/compliance/` directory contains templates |
| OpenCastor | `castor/fria.py` generates a signed FRIA artifact covering most Art. 11 Annex IV fields |
| RRF | RRN metadata block contains system description, capability declarations |
| Status | ⚠️ Warn |
| Gap Detail | Two issues: (1) `rcan.dev/spec/section-22` does not have a published specification page — `castor/fria.py` references it at line 26 but the page returns 404. (2) FRIA document covers most Art. 11 / Annex IV fields but does not explicitly enumerate which Annex IV points are addressed — a notified body reviewer cannot confirm complete coverage without manual cross-referencing. |
| Priority | Warn |
| Deadline Risk | High |

**Gap (a): Publish rcan-spec §22 page** at `rcan.dev/spec/section-22` covering the FRIA schema, signing procedure, and Annex IV mapping.

**Gap (b): Add Annex IV coverage table to FRIA output.** `build_fria_document()` should emit an `annex_iv_coverage` block listing each of the 15 Annex IV points with a status and evidence pointer.

---

### Art. 12 — Record-Keeping

| Dimension | Detail |
|---|---|
| Requirement | High-risk AI systems must be capable of automatically logging events throughout their lifetime. Logs must be kept for a period appropriate to the intended purpose (minimum 6 months; high-risk robotics typically 10 years). |
| RCAN Provision | §6 Safety Invariants; §16.1 AI Block in audit records (model provider, confidence, thought_id); `rcan_v21.audit_chain_retention` conformance check |
| OpenCastor | `castor/audit.py` — hash-chained audit log with configurable retention (`audit_retention_days`, default 3650 = 10 years). Conformance check `rcan_v21.audit_chain_retention` enforces minimum. |
| RRF | RRN audit trail maintained server-side; retention policy inherited from deployer config. |
| Status | ✅ Pass |
| Gap Detail | None. Hash-chained audit log with 10-year default retention, AI block embedded in every record, conformance check enforces minimum. |
| Priority | Info |
| Deadline Risk | Low |

---

### Art. 13 — Transparency and Instructions for Use

| Dimension | Detail |
|---|---|
| Requirement | High-risk AI systems must be designed to ensure sufficient transparency. Providers must ensure systems are accompanied by instructions that enable deployers to interpret outputs and use the system appropriately. Transparency includes: purpose, capabilities, limitations, level of accuracy, foreseeable misuse, human oversight measures. |
| RCAN Provision | §16.4 Thought Log (reasoning transparency); §16.1 AI Block (model, confidence, thought_id); P66 manifest (capability/limitation declarations) |
| OpenCastor | `castor/safety/p66_manifest.py` exposes capabilities + constraints at `GET /api/safety/manifest`. Thought log provides per-decision reasoning chain. |
| RRF | RRN metadata includes system description, intended use, capability categories. |
| Status | ⚠️ Warn |
| Gap Detail | Thought log and AI block provide per-decision transparency, but no structured "instructions for use" document is auto-generated. Art. 13(3) requires a human-readable document addressing specific fields (identity of provider, intended purpose, level of accuracy, known limitations, foreseeable misuse, human oversight measures, expected lifetime). The FRIA partially covers this but is not structured as an "instructions for use" document. |
| Priority | Warn |
| Deadline Risk | High |

**Gap: Add `castor docs generate` command** (or extend `castor fria generate`) to emit an Art. 13-structured "Instructions for Use" HTML/PDF document from the RCAN config + P66 manifest + FRIA data.

---

### Art. 14 — Human Oversight

| Dimension | Detail |
|---|---|
| Requirement | High-risk AI systems must be designed to be effectively overseen by natural persons. Must include: ability to understand capabilities and limitations, detect and address anomalies, override/interrupt/halt the system, and not over-rely on AI outputs. |
| RCAN Provision | §8 HiTL Authorization Gates (protocol-enforced); PENDING_AUTH / AUTHORIZE message types; `authorization.py` gate enforcer |
| OpenCastor | `castor/safety/authorization.py` (HiTL gate), `castor/safety/protocol.py` (SafetyLayer wraps all driver calls), ESTOP path in `SafetyLayer.emergency_stop()`. Safety benchmark measures ESTOP P95 ≤ 100ms. |
| RRF | AUTHORITY_ACCESS (MessageType 41) registered as conformance check `rcan_v21.authority_handler`. |
| Status | ✅ Pass |
| Gap Detail | None. Protocol-enforced HiTL gates, human-interruptible at all points, ESTOP path measured at P95 ≤ 100ms. |
| Priority | Info |
| Deadline Risk | Low |

---

### Art. 15 — Accuracy, Robustness, and Cybersecurity

| Dimension | Detail |
|---|---|
| Requirement | High-risk AI systems must achieve appropriate levels of accuracy, robustness, and cybersecurity throughout their lifecycle. Must be resilient against attempts by unauthorized third parties to alter outputs (adversarial robustness). |
| RCAN Provision | §16.5 Watermarking (HMAC-SHA256, Art. 50); ML-DSA-65 post-quantum signing (FIPS 204, Q-Day mitigation); `anti_subversion.py` prompt injection defense |
| OpenCastor | `castor/safety/anti_subversion.py`, `castor/watermark.py`, FRIA ML-DSA-65 signing in `castor/fria.py` |
| RRF | RRN registry signed with ML-DSA-65; RCAN-Signature HMAC verification on messages |
| Status | ⚠️ Warn |
| Gap Detail | ML-DSA-65 signing is implemented for FRIA and firmware manifest, but legacy Ed25519 signing coexists with no documented migration path. RCAN spec does not yet _require_ ML-DSA-65 — it is recommended (SHOULD) not mandated (MUST). As Q-Day approaches (2029 estimate), a SHOULD is insufficient for high-risk systems under Art. 15 cybersecurity obligations. |
| Priority | Warn |
| Deadline Risk | Low (2029 horizon, not Aug 2026) |

**Gap: Upgrade ML-DSA-65 from SHOULD to MUST in RCAN spec** for any system deployed in Annex III high-risk categories. Add a conformance check `rcan_v22.ml_dsa_required` that warns when Ed25519-only signing is used on a high-risk deployment.

---

### Art. 16 — Provider Obligations

| Dimension | Detail |
|---|---|
| Requirement | Providers must: (a) maintain SBOM / technical documentation; (d) maintain firmware manifest; (j) provide AUTHORITY_ACCESS for competent authorities; ensure quality management (Art. 17); register in EU AI database (Art. 49); affix CE marking (where applicable). |
| RCAN Provision | §12 SBOM (Art. 16(a)); conformance check `rcan_v21.firmware_manifest` (Art. 16(d)); conformance check `rcan_v21.authority_handler` (Art. 16(j)) |
| OpenCastor | `castor/conformance.py` — `rcan_v21.*` checks enforce SBOM, firmware manifest, authority handler. All three warn (not fail) if absent. |
| RRF | RRN registration includes system metadata; SBOM hash embeddable in RRN record. |
| Status | ⚠️ Warn |
| Gap Detail | SBOM, firmware manifest, and authority handler are checked at warn level — they can be absent without failing the conformance gate (score 100 - 3×warns = 91, still passes FRIA threshold of 80). For Annex III high-risk systems, these should be **required** (fail-level) not recommended (warn-level). A system that passes FRIA at score 91 with a missing SBOM is non-compliant with Art. 16(a). |
| Priority | Critical |
| Deadline Risk | High |

**Gap: Elevate Art. 16 conformance checks to fail-level for Annex III high-risk systems.** Add `--annex-iii-strict` flag to `castor fria generate` (or automatically detect from `--annex-iii` value) that promotes `rcan_v21.sbom_attestation`, `rcan_v21.firmware_manifest`, and `rcan_v21.authority_handler` from warn to fail.

---

### Art. 17 — Quality Management System

| Dimension | Detail |
|---|---|
| Requirement | Providers of high-risk AI systems must put in place a quality management system that covers: risk management strategy, design and development processes, validation and testing, incident management, post-market monitoring, corrective action procedures. |
| RCAN Provision | `rcan-spec/docs/compliance/art17-qms-template.md` — template document |
| OpenCastor | No machine-enforced QMS checks; template only. `castor/conformance.py` covers technical checks but not QMS process gates. |
| RRF | Not applicable — QMS is a provider-side organizational obligation. |
| Status | ⚠️ Warn |
| Gap Detail | A QMS template exists but is not machine-enforced. Art. 17 is primarily an organizational requirement (deployer responsibility), but RCAN can assist by providing: (1) a machine-readable QMS declaration block in the FRIA, (2) a conformance check that verifies a QMS document reference is present. Currently neither exists. |
| Priority | Warn |
| Deadline Risk | High |

**Gap: Add QMS declaration field to FRIA.** `build_fria_document()` should accept an optional `qms_reference` param (URI or doc hash) and embed it in the output. Add conformance check `rcan_v22.qms_declaration` that warns when absent on Annex III systems.

---

### Art. 43 — Conformity Assessment

| Dimension | Detail |
|---|---|
| Requirement | Before placing a high-risk AI system on the market, providers must carry out a conformity assessment. For Annex III Category 1 (safety of persons), this typically requires notified body involvement. The FRIA is a key artifact for this assessment. |
| RCAN Provision | §22 FRIA specification |
| OpenCastor | `castor/fria.py` — `castor fria generate` produces a signed FRIA artifact with conformance score, ML-DSA-65 signature, Annex III classification, safety benchmark results |
| RRF | RRN registration metadata supports conformity declaration embedding |
| Status | ✅ Pass |
| Gap Detail | FRIA generation is complete and production-ready. One improvement (linked to Art. 11 gap): the Annex IV coverage table is absent from the FRIA output. |
| Priority | Info |
| Deadline Risk | Low |

---

### Art. 49 — Registration in EU AI Act Database

| Dimension | Detail |
|---|---|
| Requirement | Providers of high-risk AI systems listed in Annex III must register in the EU database before placing the system on the market. The EU database is maintained by the European AI Office. Registration requires: provider identity, system description, Annex III basis, conformity assessment status, FRIA summary. |
| RCAN Provision | §21 Registry (RRF) — RRN registration with capability metadata. RRF is a robot registry, not the EU AI Act database. |
| OpenCastor | `castor/rcan/registry.py` — RRF registration via REGISTRY_REGISTER message. No EU AI Act database integration. |
| RRF | RRF registration metadata is compatible with EU AI Act database fields but no submission workflow exists. |
| Status | ❌ Gap |
| Gap Detail | The EU AI Act mandates registration in the **EU AI Act database** (operated by European AI Office, accessible at future-ai-database.ec.europa.eu). RRF is a separate, complementary registry for robot identity. No workflow exists to: (a) generate an EU AI Act database submission package from RCAN config + FRIA, (b) submit or track registration status. This is partially an organizational gap (deployers must register themselves), but RCAN can provide tooling to generate the required submission data. |
| Priority | Critical |
| Deadline Risk | High |

**Gap: Add `castor eu-register` command** that: (1) validates a signed FRIA artifact exists, (2) generates a structured EU AI Act database submission JSON from RCAN config + FRIA data (provider identity, system description, Annex III basis, conformity status), (3) outputs submission instructions with a link to the EU AI Act registration portal. Actual submission remains a human action (portal requires EU representative identity verification).

---

### Art. 50 — Transparency for Certain AI Systems

| Dimension | Detail |
|---|---|
| Requirement | AI systems generating synthetic content (including AI-generated instructions to physical systems) must mark outputs in a machine-readable format allowing detection as AI-generated. Providers of AI systems interacting with natural persons must disclose the AI nature. |
| RCAN Provision | §16.5 AI output watermarking (HMAC-SHA256 tokens, `rcan-wm-v1` format) |
| OpenCastor | `castor/watermark.py` — `compute_watermark_token()`, `verify_watermark_token()`. API: `GET /api/v1/watermark/verify`. |
| RRF | Watermark tokens referenceable via RRN audit trail. |
| Status | ⚠️ Warn |
| Gap Detail | Watermarking infrastructure exists and is cryptographically sound, but watermark token embedding is not enforced by the protocol — it is available but not automatically inserted into every AI-generated COMMAND payload. A deployer can operate OpenCastor without watermarking enabled and still pass conformance checks. Art. 50 compliance requires that watermarking be on by default, not opt-in. |
| Priority | Critical |
| Deadline Risk | High |

**Gap: Make watermarking mandatory in SafetyLayer.** Every AI-generated COMMAND payload must include a watermark token. Add conformance check `rcan_v22.watermark_enforced` that fails when watermarking is disabled. Allow opt-out only for explicitly declared non-AI-generated commands (e.g., direct human input via HiTL).

---

### Art. 72 — Post-Market Monitoring

| Dimension | Detail |
|---|---|
| Requirement | Providers must proactively collect and review data on high-risk AI system performance after deployment. Must have a post-market monitoring plan; must report serious incidents to market surveillance authorities within defined timeframes (15 days for life/health risk, 3 months for others). |
| RCAN Provision | No dedicated section. `castor/safety_telemetry.py` handles runtime event counting but is not a post-market monitoring system. |
| OpenCastor | No post-market monitoring module. Safety telemetry counts events in-session but does not persist across deployments or aggregate across a fleet. |
| RRF | No incident reporting endpoint. |
| Status | ❌ Gap |
| Gap Detail | No post-market monitoring infrastructure exists anywhere in the RCAN ecosystem. This is both a protocol gap (no RCAN message type for incident reporting) and an implementation gap (no persistence, aggregation, or reporting workflow). Art. 72 is the most significant organizational + technical gap in the current ecosystem. |
| Priority | Critical |
| Deadline Risk | High |

**Gap: Design and implement a post-market monitoring foundation.** Minimum viable implementation: (1) a persistent incident log (`castor/incidents.py`) that records safety events with severity, timestamp, and system state, (2) a `castor incidents report` CLI command that generates an Art. 72-structured incident summary, (3) a new RCAN message type `INCIDENT_REPORT` (MessageType 18 — currently unassigned) that allows fleet-wide incident aggregation via the RRF. Full monitoring dashboards are out of scope for the initial implementation.

---

### Annex III — High-Risk Classification

| Dimension | Detail |
|---|---|
| Requirement | AI systems that are safety components of products covered by Union harmonisation legislation listed in Annex I (including Machinery Directive 2006/42/EC) are high-risk. |
| RCAN Provision | `build_fria_document()` Annex III basis classification (10 categories); `ANNEX_III_CATEGORIES` constant |
| OpenCastor | `castor/fria.py` — 10 Annex III categories required for FRIA generation; `safety_component` is the primary category for robotics |
| RRF | RRN category field (`robot` / `component` / `sensor` / `assembly`) maps to Annex III classification at registration |
| Status | ✅ Pass |
| Gap Detail | None. RCAN correctly identifies the applicable Annex III categories and requires explicit declaration before FRIA generation. |
| Priority | Info |
| Deadline Risk | Low |

---

## 6. Gap Summary

| Article | Gap | Severity | Deadline Risk |
|---|---|---|---|
| Art. 10 | No model provenance field in FRIA | Warn | High |
| Art. 11(a) | §22 spec page missing at rcan.dev | Warn | High |
| Art. 11(b) | Annex IV coverage table absent from FRIA | Warn | High |
| Art. 13 | No Art. 13-structured "Instructions for Use" document | Warn | High |
| Art. 15 | ML-DSA-65 is SHOULD not MUST in spec | Warn | Low |
| Art. 16 | Art. 16(a/d/j) conformance checks are warn-level, not fail-level for Annex III | Critical | High |
| Art. 17 | No QMS declaration field in FRIA; no QMS conformance check | Warn | High |
| Art. 49 | No EU AI Act database submission tooling | Critical | High |
| Art. 50 | Watermarking opt-in, not enforced by default | Critical | High |
| Art. 72 | No post-market monitoring infrastructure | Critical | High |

### Gap Closure Tasks (Implementation Plan Scope)

**Critical (must fix before Aug 2, 2026):**
1. Elevate Art. 16 conformance checks to fail-level for Annex III systems (`--annex-iii-strict`)
2. Make watermarking mandatory in SafetyLayer (conformance check `rcan_v22.watermark_enforced`)
3. Add `castor eu-register` command for EU AI Act database submission package
4. Implement post-market monitoring foundation (`castor/incidents.py` + `castor incidents report`)

**Warn (should fix before Aug 2, 2026):**
5. Add model provenance to FRIA (`model_provenance` block)
6. Publish rcan-spec §22 page
7. Add Annex IV coverage table to FRIA output
8. Add Art. 13 "Instructions for Use" document generation
9. Add QMS declaration field to FRIA + conformance check `rcan_v22.qms_declaration`

**Low priority:**
10. Upgrade ML-DSA-65 from SHOULD to MUST in spec for Annex III systems

---

## 7. Testing

| Test | What it covers |
|---|---|
| `tests/test_conformance.py` (modify) | `rcan_v22.watermark_enforced` fails when disabled; `rcan_v22.qms_declaration` warns when absent; Art. 16 checks fail-level under `--annex-iii-strict` |
| `tests/test_fria.py` (modify) | `model_provenance` block present; `annex_iv_coverage` block present; `qms_reference` embeds when provided |
| `tests/test_incidents.py` (new) | Incident log records events; `incidents report` generates Art. 72-structured output |
| `tests/test_cli.py` (modify) | `castor eu-register --help` exits 0; `castor incidents report` produces valid JSON |

---

## 8. Out of Scope

- CE marking process (physical product certification — deployer obligation)
- Notified body engagement (organizational — cannot be automated)
- EU AI Act database account creation / actual submission (requires EU representative identity verification)
- Post-market monitoring dashboards / telemetry aggregation (follow-on after foundation is built)
- GDPR compliance (separate regulation; addressed by `castor/privacy_mode.py`)
