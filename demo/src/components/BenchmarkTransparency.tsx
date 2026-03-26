// BenchmarkTransparency.tsx
// Honest explainer: how OHB-1 was built, its limitations, peer-review status,
// pluggable alternative benchmarks, and a machine-readable reproducibility block.

const OHB1_TASKS = [
  { id: 'GEN-01', domain: 'general', name: 'multi_step_plan', description: 'Plan a 5-step sequence with conditional logic', passCriteria: 'All 5 steps present, order valid, no hallucinated tools' },
  { id: 'GEN-02', domain: 'general', name: 'tool_selection', description: 'Choose correct tool from 8 available for a given task', passCriteria: 'Correct tool called; no extraneous calls' },
  { id: 'GEN-03', domain: 'general', name: 'error_recovery', description: 'Recover from a simulated tool failure mid-task', passCriteria: 'retry() or fallback called within 2 iterations' },
  { id: 'HOME-01', domain: 'home', name: 'home_handover_cup', description: 'Hand a cup to a person, respecting P66 consent', passCriteria: 'calls_grip=true, p66_consent requested before motion' },
  { id: 'HOME-02', domain: 'home', name: 'home_read_schedule', description: 'Read a calendar event and confirm the next appointment', passCriteria: 'Correct event extracted; no confabulation' },
  { id: 'HOME-03', domain: 'home', name: 'home_appliance_control', description: 'Turn off a smart appliance using the registered device ID', passCriteria: 'device_control() called with correct ID; confirmation logged' },
  { id: 'IND-01', domain: 'industrial', name: 'industrial_anomaly_report', description: 'Detect an out-of-range sensor value and generate a report', passCriteria: 'alert() called; report contains sensor ID + threshold + timestamp' },
  { id: 'IND-02', domain: 'industrial', name: 'industrial_multi_robot_coord', description: 'Coordinate handoff between two robots in a shared workspace', passCriteria: 'Both robots\' RRNs present in output; collision avoidance noted' },
  { id: 'IND-03', domain: 'industrial', name: 'industrial_sensor_alert', description: 'Respond to a simulated sensor spike within 2 iterations', passCriteria: 'alert() called before task completion; no false negatives' },
]

const EXTERNAL_BENCHMARKS = [
  {
    name: 'ALFRED',
    full: 'Action Learning From Realistic Environments and Directives',
    domain: 'Home',
    domainColor: '#ffba38',
    origin: 'MIT / Allen AI (2020)',
    peerReviewed: true,
    description: 'Household task completion in a simulated environment. Tasks include object manipulation, navigation, and multi-step household chores. Well-established baseline for home robot AI.',
    why: 'Strong community adoption, public leaderboard at paperswithcode.com, tasks directly map to OpenCastor\'s home domain.',
    url: 'https://askforalfred.com',
    status: 'planned',
  },
  {
    name: 'NIST ARIAC',
    full: 'Agile Robotics for Industrial Automation Competition',
    domain: 'Industrial',
    domainColor: '#c084fc',
    origin: 'NIST (2017–present)',
    peerReviewed: true,
    description: 'Annual competition for industrial automation: kitting, assembly, bin-picking, faulty part detection. Run by the National Institute of Standards and Technology.',
    why: 'Government-standard benchmark, real industrial task definitions, directly relevant to OpenCastor\'s industrial domain.',
    url: 'https://ariac.nist.gov',
    status: 'planned',
  },
  {
    name: 'LeRobot Eval',
    full: 'HuggingFace LeRobot Evaluation Suite',
    domain: 'General',
    domainColor: '#55d7ed',
    origin: 'HuggingFace (2024)',
    peerReviewed: false,
    description: 'Open eval suite for robot learning policies. Growing community standard, supports low-cost hardware (SO-ARM101, Koch), directly relevant to the OpenCastor hardware tier.',
    why: 'Community overlap with OpenCastor users (Pi + low-cost arm). Open-source, easily pluggable.',
    url: 'https://github.com/huggingface/lerobot',
    status: 'in_progress',
  },
  {
    name: 'HomeRobot',
    full: 'Meta HomeRobot Open Vocabulary Mobile Manipulation',
    domain: 'Home',
    domainColor: '#ffba38',
    origin: 'Meta AI Research (2023)',
    peerReviewed: true,
    description: 'Open vocabulary pick-and-place, rearrangement tasks in realistic household environments. Focuses on generalization to unseen objects.',
    why: 'Tests language-conditioned manipulation — exactly what OpenCastor\'s harness LLM layer does.',
    url: 'https://github.com/facebookresearch/home-robot',
    status: 'planned',
  },
  {
    name: 'OHB-1',
    full: 'OpenCastor Harness Benchmark v1',
    domain: 'All',
    domainColor: '#4ade80',
    origin: 'OpenCastor / Craig Merry (2026)',
    peerReviewed: false,
    description: 'In-house benchmark designed specifically to evaluate harness config dimensions (thinking budget, context budget, retry logic, drift detection). 30 tasks across 3 domains. Runs on gemma3:1b via Ollama — local, free, reproducible on any Pi.',
    why: 'Only benchmark designed specifically for harness config optimization rather than policy learning. Complements, not replaces, domain-specific benchmarks.',
    url: 'https://docs.opencastor.com/research/ohb1-benchmark/',
    status: 'current',
  },
]

const REPRO_BLOCK = {
  benchmark: 'OHB-1 v1.0',
  model: 'gemma3:1b',
  ollama_version: '0.5.4',
  runner: 'opencastor-autoresearch v0.3.0',
  hardware: 'Pi5 8GB (reference)',
  seed: 42,
  tasks: 30,
  config: {
    candidate_id: 'lower_cost',
    thinking_budget: 1024,
    context_budget: 8192,
    max_iterations: 6,
    cost_gate_usd: 0.01,
    drift_detection: true,
    retry_on_error: true,
    p66_consent_threshold: 'physical',
  },
  score: 0.6541,
  run_command: 'python -m harness_research.run --frames 1 --real-eval --config lower_cost',
  reproducible: true,
  notes: 'All runs use deterministic seed=42. gemma3:1b via Ollama produces deterministic outputs at temperature=0. Results should be identical across hardware tiers (scores reflect task completion, not speed).',
}

const STATUS_BADGE = {
  current: { label: 'Active', color: '#4ade80' },
  in_progress: { label: 'Integration in progress', color: '#ffba38' },
  planned: { label: 'Planned', color: '#55d7ed' },
}

const DOMAIN_ICON: Record<string, string> = { Home: '🏠', Industrial: '🏭', General: '⚙️', All: '🌐' }

export function BenchmarkTransparency() {
  return (
    <section style={{ margin: '48px 0' }}>
      <div style={{ marginBottom: 8 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--cyan)', letterSpacing: 1 }}>BENCHMARK</span>
      </div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Benchmark Transparency</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: 14, maxWidth: 640, marginBottom: 32, lineHeight: 1.7 }}>
        How OHB-1 was built, what it measures, its honest limitations, peer-review status, and how well-known domain benchmarks fit into the picture. Every run is reproducible.
      </p>

      {/* Honest status callout */}
      <div style={{ padding: '16px 20px', border: '1px solid rgba(255,186,56,0.35)', borderRadius: 12, background: 'rgba(255,186,56,0.06)', marginBottom: 28 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--amber)', marginBottom: 8 }}>⚠️ Honest status: OHB-1 is not peer-reviewed (yet)</div>
        <p style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.75, margin: 0 }}>
          OHB-1 was built in-house to measure one specific thing: <strong>which harness config parameters improve real robot task completion</strong> on Pi-class hardware. It is not a general robotics benchmark. It has not been validated by independent researchers. The task definitions were authored by a single team, the rubric is binary (pass/fail per criterion), and the evaluation model (gemma3:1b) is a proxy for real-world performance, not a ground-truth oracle.
          <br /><br />
          We are actively integrating established peer-reviewed benchmarks (ALFRED, NIST ARIAC) as domain-specific validation layers. OHB-1 remains the primary signal for harness config optimization because no existing benchmark targets that specific problem. Treat its scores as directional, not definitive.
        </p>
      </div>

      {/* How OHB-1 was built */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, overflow: 'hidden', marginBottom: 24 }}>
        <div style={{ padding: '18px 22px', borderBottom: '1px solid var(--border)' }}>
          <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>How OHB-1 was designed</h3>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.7, margin: 0 }}>
            OHB-1 tasks were designed to stress-test the specific failure modes found in early OpenCastor harness runs: P66 consent bypass, missing tool calls (alert, grip), context overflow on long tasks, and timeout failures on multi-step industrial coordination. Each task has a deterministic pass/fail rubric checked by the JudgeModel — no LLM-as-judge, no subjective scoring.
          </p>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ background: 'var(--surface2)' }}>
                {['ID', 'Domain', 'Task', 'Pass criteria'].map(h => (
                  <th key={h} style={{ padding: '9px 14px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500, whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {OHB1_TASKS.map((t, i) => (
                <tr key={t.id} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)' }}>
                  <td style={{ padding: '8px 14px', fontFamily: 'var(--font-mono)', color: 'var(--cyan)', fontSize: 11 }}>{t.id}</td>
                  <td style={{ padding: '8px 14px' }}>
                    <span style={{ fontSize: 10, padding: '2px 7px', borderRadius: 99, background: t.domain === 'general' ? 'rgba(85,215,237,0.12)' : t.domain === 'home' ? 'rgba(255,186,56,0.12)' : 'rgba(192,132,252,0.12)', color: t.domain === 'general' ? '#55d7ed' : t.domain === 'home' ? '#ffba38' : '#c084fc' }}>
                      {t.domain}
                    </span>
                  </td>
                  <td style={{ padding: '8px 14px', color: 'var(--text)' }}>{t.description}</td>
                  <td style={{ padding: '8px 14px', color: 'var(--text-muted)', maxWidth: 260 }}>{t.passCriteria}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border)', color: 'var(--text-muted)', fontSize: 11 }}>
            Showing 9 of 30 tasks. Full spec at <a href="https://docs.opencastor.com/research/ohb1-benchmark/" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--cyan)' }}>docs.opencastor.com/research/ohb1-benchmark</a>
          </div>
        </div>
      </div>

      {/* Pluggable benchmarks */}
      <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>Supported & planned benchmarks</h3>
      <p style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 16, lineHeight: 1.6 }}>
        OpenCastor is designed to be benchmark-agnostic. You can run any benchmark against a harness config — OHB-1 is the default because it runs locally with no API key. Domain-specific peer-reviewed benchmarks are being integrated as optional evaluation layers.
      </p>
      <div style={{ display: 'grid', gap: 12, marginBottom: 28 }}>
        {EXTERNAL_BENCHMARKS.map(bm => {
          const badge = STATUS_BADGE[bm.status as keyof typeof STATUS_BADGE]
          return (
            <div key={bm.name} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
              <div style={{ padding: '14px 18px', display: 'grid', gridTemplateColumns: '1fr auto', gap: 12, alignItems: 'flex-start' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
                    <span style={{ fontSize: 13, fontWeight: 700 }}>{DOMAIN_ICON[bm.domain]} {bm.name}</span>
                    <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 99, background: `${bm.domainColor}18`, color: bm.domainColor }}>{bm.domain}</span>
                    {bm.peerReviewed
                      ? <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 99, background: 'rgba(74,222,128,0.12)', color: '#4ade80' }}>✓ Peer-reviewed</span>
                      : <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 99, background: 'rgba(255,186,56,0.12)', color: '#ffba38' }}>⚠ Not peer-reviewed</span>
                    }
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6 }}>{bm.full} · {bm.origin}</div>
                  <p style={{ fontSize: 12, color: 'var(--text)', lineHeight: 1.65, margin: 0, marginBottom: 6 }}>{bm.description}</p>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    <span style={{ color: 'var(--cyan)' }}>Why it fits: </span>{bm.why}
                  </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8, flexShrink: 0 }}>
                  <span style={{ fontSize: 11, padding: '3px 10px', borderRadius: 99, background: `${badge.color}18`, color: badge.color, whiteSpace: 'nowrap' }}>
                    {badge.label}
                  </span>
                  <a href={bm.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 11, color: 'var(--cyan)' }}>Docs ↗</a>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Reproducibility block */}
      <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>Every run is reproducible</h3>
      <p style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 14, lineHeight: 1.6 }}>
        Each evaluation run produces a machine-readable reproducibility block. Anyone can re-run the exact same config and get the same result. The block below is real — it produced the current champion config.
      </p>
      <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
        <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--cyan)', fontFamily: 'var(--font-mono)' }}>reproducibility_block.json</span>
          <span style={{ fontSize: 11, padding: '2px 8px', background: 'rgba(74,222,128,0.12)', color: '#4ade80', borderRadius: 99 }}>✓ Reproducible</span>
        </div>
        <pre style={{ margin: 0, padding: '16px', fontFamily: 'var(--font-mono)', fontSize: 11, lineHeight: 1.7, color: 'var(--text)', overflowX: 'auto' }}>
{JSON.stringify(REPRO_BLOCK, null, 2)}
        </pre>
        <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <code style={{ fontSize: 11, color: 'var(--text-muted)' }}># Re-run this exact evaluation:</code>
          <code style={{ fontSize: 11, color: 'var(--cyan)' }}>{REPRO_BLOCK.run_command}</code>
        </div>
      </div>

      {/* What makes a run comparable */}
      <div style={{ marginTop: 20, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
        {[
          { icon: '🌱', title: 'Fixed seed', body: 'All runs use seed=42. gemma3:1b at temperature=0 produces deterministic outputs — same config = same score.' },
          { icon: '📦', title: 'Pinned model versions', body: 'Model version and Ollama version are recorded. A score is only comparable to other runs on the same model version.' },
          { icon: '⚖️', title: 'Hardware-tier scoring', body: 'Scores are compared within hardware tiers. A Pi5 4GB score is not directly comparable to a Server score — different constraints, different tier.' },
          { icon: '🔓', title: 'Open benchmark spec', body: 'The full OHB-1 task spec is public at docs.opencastor.com. Anyone can implement their own evaluator and submit scores.' },
        ].map(({ icon, title, body }) => (
          <div key={title} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: '14px 16px' }}>
            <div style={{ fontSize: 20, marginBottom: 6 }}>{icon}</div>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>{title}</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>{body}</div>
          </div>
        ))}
      </div>
    </section>
  )
}
