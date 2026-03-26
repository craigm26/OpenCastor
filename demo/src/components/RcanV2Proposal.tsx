export function RcanV2Proposal() {
  const breakingChanges = [
    { alias: 'FEDERATION_SYNC', canonical: 'FLEET_COMMAND', value: 23 },
    { alias: 'ALERT', canonical: 'FAULT_REPORT', value: 26 },
    { alias: 'AUDIT', canonical: 'TRANSPARENCY', value: 16 },
  ]

  const newMessageTypes = [
    { value: 41, name: 'AUTHORITY_ACCESS', dir: 'authority → robot', desc: 'Regulator requests audit data per EU AI Act Art. 16(j)' },
    { value: 42, name: 'AUTHORITY_RESPONSE', dir: 'robot → authority', desc: 'Robot provides requested audit data' },
    { value: 43, name: 'FIRMWARE_ATTESTATION', dir: 'robot → registry', desc: 'Publishes signed firmware manifest' },
    { value: 44, name: 'SBOM_UPDATE', dir: 'robot → registry', desc: 'Publishes updated software bill of materials' },
  ]

  const conformanceLevels = [
    { level: 'L1', label: 'Core', req: 'DISCOVER, STATUS, COMMAND, RURI, JWT' },
    { level: 'L2', label: 'Secure', req: 'L1 + HiTL gates, Ed25519, AuditChain' },
    { level: 'L3', label: 'Federated', req: 'L2 + commitment chain, cross-registry' },
    { level: 'L4', label: 'Registry', req: 'L3 + REGISTER/RESOLVE, RRN validation' },
    { level: 'L5', label: 'Supply Chain', req: 'L4 + firmware verification, SBOM, EU AI Act', highlight: true },
  ]

  const timeline = [
    { date: 'May 2026', milestone: 'RFC published', desc: 'Open for community comment' },
    { date: 'Q3 2026', milestone: 'Foundation formed', desc: 'Board ratifies v2.0 scope' },
    { date: 'Aug 2026', milestone: 'EU AI Act enforcement', desc: 'Art. 16 obligations active' },
    { date: 'Oct 2026', milestone: 'Draft spec', desc: 'Complete wire-format specification' },
    { date: 'Nov 2026', milestone: 'SDK alphas', desc: 'rcan-py 1.0.0a1, rcan-ts 1.0.0a1' },
    { date: 'Dec 2026', milestone: 'Release candidate', desc: 'Conformance suite L5 finalized' },
    { date: 'Q1 2027', milestone: 'v2.0 Release', desc: 'Spec, SDKs, conformance suite', highlight: true },
  ]

  const openQuestions = [
    { q: 'RURI signing', detail: 'Should ?sig= be mandatory for all v2.0 messages, or only cross-registry?' },
    { q: 'SBOM format', detail: 'CycloneDX vs SPDX — which should be normative? Support both?' },
    { q: 'M2M authorization', detail: 'Should M2M_PEER require mutual TLS in addition to JWT?' },
    { q: 'Firmware attestation', detail: 'Re-attest on every boot, or only on firmware change?' },
    { q: 'ISO liaison', detail: 'TC 299 membership — Foundation or ContinuonAI responsibility?' },
    { q: 'MessageType range', detail: 'Reserve 41–50 for regulatory, 51–99 for extensions, or flexible?' },
    { q: 'v1.x LTS duration', detail: 'Current 3-year guarantee (until Jan 2029) — extend for installed base?' },
  ]

  return (
    <section style={{ margin: '40px 0' }}>
      <div style={{ marginBottom: 8 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: '#c084fc', letterSpacing: 1 }}>PROPOSAL · DRAFT</span>
      </div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>RCAN v2.0 Specification</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: 14, maxWidth: 600, marginBottom: 24, lineHeight: 1.7 }}>
        The first major version bump of the protocol. Breaking wire-format changes for firmware integrity,
        supply chain attestation, ISO alignment, and EU AI Act compliance. Target: Q1 2027.
      </p>

      {/* Four pillars */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginBottom: 32 }}>
        {[
          { icon: '🔐', title: 'Signed Firmware', desc: 'Ed25519-signed firmware manifests at a well-known endpoint. Provenance verification on first connection.' },
          { icon: '📦', title: 'Supply Chain (SBOM)', desc: 'CycloneDX-based software bill of materials. Every message carries an attestation reference.' },
          { icon: '🏛️', title: 'ISO/TC 299', desc: 'Normative mapping to ISO 13482, ISO 10218-2, ISO/IEC 42001. Safety invariants aligned with international standards.' },
          { icon: '🇪🇺', title: 'EU AI Act Art. 16', desc: 'Wire-level compliance for high-risk AI systems. New AUTHORITY_ACCESS message type for regulator audit.' },
        ].map(p => (
          <div key={p.title} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: '18px 16px' }}>
            <div style={{ fontSize: 20, marginBottom: 8 }}>{p.icon}</div>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>{p.title}</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>{p.desc}</div>
          </div>
        ))}
      </div>

      {/* Breaking changes */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, overflow: 'hidden', marginBottom: 24 }}>
        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: '#f87171', letterSpacing: 1, marginBottom: 4 }}>BREAKING CHANGES</div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Deprecated alias removal (v1.8 → v2.0)</div>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '8px 20px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>Deprecated</th>
              <th style={{ padding: '8px 12px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>Canonical</th>
              <th style={{ padding: '8px 20px', textAlign: 'right', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>Value</th>
            </tr>
          </thead>
          <tbody>
            {breakingChanges.map(c => (
              <tr key={c.alias} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '10px 20px', fontFamily: 'var(--font-mono)', fontSize: 12, color: '#f87171', textDecoration: 'line-through' }}>{c.alias}</td>
                <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', fontSize: 12, color: '#4ade80' }}>{c.canonical}</td>
                <td style={{ padding: '10px 20px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)' }}>{c.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* New envelope fields */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: '16px 20px', marginBottom: 24 }}>
        <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--cyan)', letterSpacing: 1, marginBottom: 12 }}>NEW REQUIRED ENVELOPE FIELDS</div>
        <code style={{ fontFamily: 'var(--font-mono)', fontSize: 12, lineHeight: 2, display: 'block' }}>
          <div><span style={{ color: 'var(--text-muted)' }}>13</span> <span style={{ color: 'var(--cyan)' }}>firmware_hash</span> <span style={{ color: 'var(--text-muted)' }}>// SHA-256 of firmware manifest (all messages)</span></div>
          <div><span style={{ color: 'var(--text-muted)' }}>14</span> <span style={{ color: 'var(--cyan)' }}>attestation_ref</span> <span style={{ color: 'var(--text-muted)' }}>// URI to SBOM document (optional)</span></div>
          <div><span style={{ color: 'var(--text-muted)' }}>15</span> <span style={{ color: 'var(--cyan)' }}>delegation_chain</span> <span style={{ color: 'var(--text-muted)' }}>// Required for COMMAND & INVOKE</span></div>
        </code>
      </div>

      {/* New message types */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, overflow: 'hidden', marginBottom: 24 }}>
        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: '#c084fc', letterSpacing: 1, marginBottom: 4 }}>NEW IN v2.0</div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Message types for regulatory compliance</div>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '8px 20px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>ID</th>
              <th style={{ padding: '8px 12px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>Name</th>
              <th style={{ padding: '8px 12px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>Direction</th>
              <th style={{ padding: '8px 20px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>Description</th>
            </tr>
          </thead>
          <tbody>
            {newMessageTypes.map(m => (
              <tr key={m.value} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '10px 20px', fontFamily: 'var(--font-mono)', fontSize: 12, color: '#c084fc' }}>{m.value}</td>
                <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--cyan)' }}>{m.name}</td>
                <td style={{ padding: '10px 12px', fontSize: 12, color: 'var(--text-muted)' }}>{m.dir}</td>
                <td style={{ padding: '10px 20px', fontSize: 12, color: 'var(--text)' }}>{m.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Conformance levels */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, overflow: 'hidden', marginBottom: 24 }}>
        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Conformance levels</div>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '8px 20px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>Level</th>
              <th style={{ padding: '8px 12px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>Label</th>
              <th style={{ padding: '8px 20px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>Key Requirement</th>
            </tr>
          </thead>
          <tbody>
            {conformanceLevels.map(c => (
              <tr key={c.level} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '10px 20px', fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, color: c.highlight ? '#c084fc' : 'var(--text-muted)' }}>{c.level}</td>
                <td style={{ padding: '10px 12px', fontSize: 12, fontWeight: c.highlight ? 700 : 400, color: c.highlight ? '#c084fc' : 'var(--text)' }}>{c.label}</td>
                <td style={{ padding: '10px 20px', fontSize: 12, color: 'var(--text-muted)' }}>{c.req}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Timeline */}
      <div style={{ marginBottom: 32 }}>
        <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--cyan)', letterSpacing: 1, marginBottom: 16 }}>TIMELINE</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          {timeline.map((t, i) => (
            <div key={t.date} style={{ display: 'flex', gap: 16, position: 'relative' }}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 20 }}>
                <div style={{
                  width: 10, height: 10, borderRadius: '50%',
                  background: t.highlight ? '#c084fc' : 'var(--border)',
                  border: t.highlight ? '2px solid #c084fc' : '2px solid var(--text-muted)',
                  flexShrink: 0, marginTop: 4,
                }} />
                {i < timeline.length - 1 && (
                  <div style={{ width: 1, flex: 1, background: 'var(--border)', minHeight: 28 }} />
                )}
              </div>
              <div style={{ paddingBottom: 20, flex: 1 }}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: t.highlight ? '#c084fc' : 'var(--cyan)', fontWeight: 600 }}>{t.date}</span>
                  <span style={{ fontSize: 13, fontWeight: 600, color: t.highlight ? '#c084fc' : 'var(--text)' }}>{t.milestone}</span>
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>{t.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Open questions — feedback prompt */}
      <div style={{ background: 'rgba(192,132,252,0.06)', border: '1px solid rgba(192,132,252,0.3)', borderRadius: 14, padding: '20px 22px', marginBottom: 24 }}>
        <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: '#c084fc', letterSpacing: 1, marginBottom: 12 }}>OPEN QUESTIONS — WE WANT YOUR FEEDBACK</div>
        <p style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.7, marginBottom: 16 }}>
          This proposal is a draft. These design decisions are still open — your input shapes the protocol:
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {openQuestions.map((oq, i) => (
            <div key={i} style={{ background: 'var(--surface)', borderRadius: 10, padding: '12px 16px' }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#c084fc', marginBottom: 4 }}>{i + 1}. {oq.q}</div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>{oq.detail}</div>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 16, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <a
            href="https://github.com/craigm26/OpenCastor/issues/new?title=[RCAN+v2.0]+Feedback&labels=rcan-v2"
            target="_blank"
            rel="noopener noreferrer"
            style={{ padding: '9px 18px', background: 'rgba(192,132,252,0.15)', border: '1px solid #c084fc', borderRadius: 8, color: '#c084fc', fontSize: 13, fontWeight: 600, fontFamily: 'var(--font-head)' }}
          >
            Submit feedback on GitHub
          </a>
          <a
            href="https://rcan.dev/spec/"
            target="_blank"
            rel="noopener noreferrer"
            style={{ padding: '9px 18px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text)', fontSize: 13, fontFamily: 'var(--font-head)' }}
          >
            Current spec (v1.9) →
          </a>
        </div>
      </div>

      {/* What this means for the leaderboard */}
      <div style={{ padding: '16px 20px', border: '1px solid rgba(85,215,237,0.3)', borderRadius: 12, background: 'rgba(85,215,237,0.05)' }}>
        <div style={{ fontSize: 13, color: 'var(--cyan)', fontWeight: 600, marginBottom: 6 }}>What this means for the leaderboard</div>
        <p style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.7, margin: 0 }}>
          v2.0 introduces L5: Supply Chain conformance. Robots that publish signed firmware manifests and SBOMs will unlock a new competition tier on the leaderboard — verifiable, auditable, and regulatory-ready. The harness research pipeline will test v2.0 compliance alongside OHB-1 benchmark scores.
        </p>
      </div>
    </section>
  )
}
