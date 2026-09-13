# Security Policy

## Supported Versions

| Version | Status | Supported Until |
|---------|--------|-----------------|
| v2026.3.x (current) | ✅ Active | Until v2026.6 released |
| v2026.2.x | 🔶 Security fixes only | 2026-09-01 |
| Earlier | ❌ End of life | — |

## Reporting a Vulnerability

**Do not file a public GitHub issue for security vulnerabilities.**

Report privately:
- **GitHub Security Advisories** (preferred): [github.com/craigm26/OpenCastor/security/advisories/new](https://github.com/craigm26/OpenCastor/security/advisories/new)
- **Email**: security@continuon.ai

Include: version affected, description, reproduction steps, impact assessment, and proposed fix if you have one.

## Response Timeline

| Stage | Commitment |
|-------|-----------|
| Acknowledgement | Within 48 hours |
| Triage | Within 7 days |
| Updates | Every 14 days |
| Critical/High patch | Within 30 days |
| Medium patch | Within 90 days |
| CVE coordination | On request |

## Scope

**In scope:**
- Prompt injection bypasses — natural language inputs that cause OpenCastor to execute unsafe motor commands
- Safety invariant bypasses — any path that allows remote commands to override on-device safety checks
- RCAN RBAC bypass — ways to issue commands beyond a principal's declared role
- Authentication weaknesses — token forgery, session fixation, credential exposure
- Audit log tampering — attacks against the commitment chain or HMAC integrity
- §16 AI accountability bypass — ways to issue AI commands without model identity being recorded
- HiTL gate bypass — paths that execute PENDING_AUTH commands without authorization
- Dependency vulnerabilities with direct exploitability (not just theoretical)

**Physical safety vulnerabilities are treated as Critical regardless of exploitability complexity.** Any finding that could cause a robot to move unsafely is immediately escalated.

**Out of scope:**
- Theoretical vulnerabilities without a realistic attack path
- Hardware-level attacks requiring physical access (out of software scope)
- Social engineering
- Denial of service that does not affect physical safety

## Dependency Vulnerabilities

OpenCastor uses Dependabot for automated dependency scanning. If you discover a vulnerability in a dependency before Dependabot flags it, please report it privately — we'll coordinate with the upstream maintainer.

## SBOM

A Software Bill of Materials (CycloneDX 1.6 JSON) is attached to each release. Verify the SBOM hash matches the release artifact before use in production environments.

## Physical Safety Disclosure Policy

Vulnerabilities that could cause physical harm (uncontrolled robot motion, safety system bypass) follow a **shorter embargo**: we commit to a patch within **14 days** and will coordinate disclosure with the reporter, relevant hardware vendors, and CISA if warranted.

## CVE Process

Critical and High findings: we request a CVE via GitHub's CVE numbering authority partnership and coordinate a 90-day maximum embargo with the reporter.

## RCAN Integration

OpenCastor is the reference runtime for the [RCAN protocol](https://rcan.dev). The following RCAN-specific security surfaces apply:

### Commitment Chain
Each robot action can be recorded as an HMAC-chained entry in the commitment log (`~/.local/share/opencastor/commitments.jsonl` by default, XDG-compliant). Chain links can be checked with:
```bash
rcan-validate audit ~/.local/share/opencastor/commitments.jsonl
```

**There is no default HMAC secret, and the chain is off until a key is provisioned.** Releases before 3.5 fell back to a literal secret compiled into the wheel whenever `OPENCASTOR_COMMITMENT_SECRET` was unset, and this document described that fallback as an acceptable development default. It was not one. The variable appeared in no template and no generated unit, so the fallback was not a development case at all: it was the shipped state of every install, sealing every action record with a key that anyone who had the package also had. A seal that anyone can recompute is not evidence of anything, and a log full of such seals is worse than no log, because it reads as though it were.

The key is now a generated default. `castor up` mints 32 random bytes into `<robot home>/keys/commitment.key` at mode 0600 and renders `OPENCASTOR_COMMITMENT_SECRET_FILE` into the generated castor unit. Nobody edits a file on the robot to get this. A robot that has never run `castor up` since 3.5 has no key, and on that robot `CommitmentChain.enabled` is False, `append_action` returns None after one warning, and nothing is written. That is the intended behaviour: no key, no record, and the tooling says so rather than producing records it cannot stand behind.

Resolution order for the secret: the `secret=` argument, then `$OPENCASTOR_COMMITMENT_SECRET`, then `$OPENCASTOR_COMMITMENT_SECRET_FILE`, then nothing.

Treat the key file the way you treat a private key. Anyone who can read it can both verify and forge a record. Keeping it on the robot means the chain detects a change made by someone who never had shell on the robot, and nothing more than that. It is not an outside party's verification, and no OpenCastor command renders one.

### Confidence Gates
`agent.confidence_gates` in the RCAN config are a configurable safety surface — actions below the configured threshold are automatically blocked. Misconfigured or absent gates may allow low-confidence AI actions to execute unvetted.

### HiTL Gates
`agent.hitl_gates` require human approval before executing flagged action classes. Bypassing the approval channel (e.g., by setting `auto_approve: true` in a low-security environment) removes a critical safety layer.

### Thought Log
The AI thought log (`ThoughtLog`) records full reasoning traces as JSONL. These files may contain sensitive operational context. Restrict read access appropriately (`chmod 600`).

### Compliance Reports
`castor compliance` generates audit reports against RCAN §16 (AI Accountability). Reports are informational — they do not replace a security audit.

---

## Responsible Disclosure Hall of Fame

*(None yet — be the first.)*

---

Policy aligned with ISO/IEC 29147 (vulnerability disclosure), ISO/IEC 30111 (vulnerability handling), and CISA coordinated vulnerability disclosure guidelines.
