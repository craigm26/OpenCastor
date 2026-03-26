// ── Optimization Guide ───────────────────────────────────────────────────────
// Explains every lever available to the human + the robot's auto-optimization.
// Answers: what can I tune? what does the system do for me? how do I win?

const HUMAN_LEVERS = [
  {
    lever: 'Hardware tier',
    where: 'Physical choice',
    impact: 'High',
    impactColor: '#f87171',
    description: 'The biggest single variable. Pi5+Hailo8L runs NPU-accelerated inference at ~0.3s latency. Pi5 4GB with gemma3:1b is slower but fully local. Server/Cloud has no memory constraint. Your hardware tier determines which competition you enter.',
    tips: ['Pi5+Hailo8L: highest edge score ceiling. Best for latency-sensitive home tasks.', 'Pi5 8GB: sweet spot for community contribute runs.', 'Server/Cloud: highest OHB-1 scores, but credits are less rare — the Pi community values edge wins.'],
  },
  {
    lever: 'Model selection',
    where: 'arm.rcan.yaml → agent.model',
    impact: 'High',
    impactColor: '#f87171',
    description: 'Cloud models (claude-sonnet, gemini-2.5-pro) score higher on general reasoning and industrial tasks. Local models (gemma3:1b, llama3.2:3b) win on home tasks where latency matters more than raw quality. You choose which model your robot uses for contribute runs.',
    tips: ['gemini-2.5-flash: best cloud-tier value — high score, low cost.', 'gemma3:1b: best local model for Pi5 4GB — runs in <1GB RAM.', 'llama3.2:3b: better reasoning than gemma3:1b on multi-step tasks, needs Pi5 8GB+.'],
  },
  {
    lever: 'thinking_budget',
    where: 'arm.rcan.yaml → agent.thinking_budget',
    impact: 'Medium',
    impactColor: '#ffba38',
    description: 'How many tokens the model uses for internal reasoning before answering. Higher = better quality, higher cost, slower response. Lower = faster, cheaper, but more errors on complex tasks. The autoresearch pipeline will find the optimal value for your hardware — but you can set a floor.',
    tips: ['Pi5 4GB: set floor at 256 to avoid timeout failures.', 'Pi5 8GB: 512–1024 is the sweet spot from benchmark data.', 'Server: 2048–4096 produces measurable score gains on industrial tasks.'],
  },
  {
    lever: 'context_budget',
    where: 'arm.rcan.yaml → agent.context_budget',
    impact: 'Medium',
    impactColor: '#ffba38',
    description: 'How much conversation history the model sees. Larger context = better task continuity for multi-step scenarios, but increases RAM usage and API cost. On memory-constrained hardware, too-large context causes OOM errors that crash the evaluation.',
    tips: ['Pi5 4GB: hard cap at 4096 — higher causes OOM on complex tasks.', 'Pi5 8GB: 8192 is stable; 16384 is viable if RAM is dedicated to the robot.', 'Server: 32768 is the current research sweet spot for industrial multi-robot coord.'],
  },
  {
    lever: 'Contribute mode',
    where: 'App → Settings → Contribute',
    impact: 'Medium',
    impactColor: '#ffba38',
    description: 'Personal mode runs evaluations locally but results are private (no credit, no leaderboard). Community mode shares results with the fleet — you earn Castor Credits and your robot appears on the leaderboard. You can switch at any time. P66 safety rules apply in both modes.',
    tips: ['Use Personal mode for testing config changes before competing.', 'Switch to Community mode before Swarm competitions to earn credits.', 'Community mode requires you to agree to the OpenCastor research terms.'],
  },
  {
    lever: 'Idle time scheduling',
    where: 'arm.rcan.yaml → agent.contribute.schedule',
    impact: 'Low',
    impactColor: '#4ade80',
    description: 'You can restrict contribute runs to specific hours — overnight, weekends, or anytime the robot is not in active use. More idle hours = more work units = more credits. The schedule is a cron expression.',
    tips: ['Overnight (23:00–07:00) is when most robots run — pool is competitive.', 'Off-peak hours: less competition, same credits per work unit.', 'P66 absolute: if a live command arrives, contribute is preempted immediately.'],
  },
]

const AUTO_OPTIMIZATIONS = [
  {
    name: 'Config Generator',
    icon: '🔬',
    who: 'opencastor-autoresearch',
    description: 'Systematically generates candidate harness configs from the 263,424-config search space. Uses a grid search with coverage prioritization — ensuring unexplored regions of the space are evaluated before repeating similar configs.',
    what_it_does: 'Produces a new candidate config for each evaluation run. Covers 9 config dimensions: model family, thinking budget, context budget, max iterations, cost gate, drift detection, retry logic, pattern engine, memory strategy.',
  },
  {
    name: 'OHB-1 Evaluator',
    icon: '📋',
    who: 'opencastor-autoresearch / your robot',
    description: 'Runs 30 real robot tasks against the candidate config and scores each on a binary rubric. Did it call the right tools? Did it produce a grippable output? Did it respect P66? Did it call alert() when needed?',
    what_it_does: 'Produces a score (0–1) per task, domain score (general/home/industrial), and an overall OHB-1 score. Takes ~3 minutes per config on Pi5 4GB. Runs locally — no cloud required.',
  },
  {
    name: 'JudgeModel',
    icon: '⚖️',
    who: 'opencastor-autoresearch',
    description: 'A deterministic rubric checker that evaluates model outputs without needing an LLM judge. Checks 5 binary criteria per task, blended with raw completion scores at 30% weight. Fast, reproducible, zero API cost.',
    what_it_does: 'Adds 0.30× blend of rubric score to the raw completion score. Catches cases where the model technically completes a task but in a way a real robot controller would reject (e.g., no grip call before object handover).',
  },
  {
    name: 'Drift Detection',
    icon: '📡',
    who: 'OpenCastor runtime (on your robot)',
    description: 'Monitors the model\'s output entropy over time. When drift_detection=true, if the model starts giving systematically different outputs compared to its baseline (e.g., due to a model update or context poisoning), the runtime flags it and resets the session.',
    what_it_does: 'Prevents long-running robot sessions from degrading silently. Particularly important for home and industrial tasks where consistency matters more than peak performance.',
  },
  {
    name: 'Retry Logic',
    icon: '🔄',
    who: 'OpenCastor runtime (on your robot)',
    description: 'When retry_on_error=true, the runtime retries failed tool calls up to max_iterations times before giving up. This is the single config flag most correlated with industrial score improvement (+12% median across all tiers in the benchmark data).',
    what_it_does: 'Catches transient API failures, timeouts, and malformed outputs. On industrial tasks (sensor_alert, anomaly_report), retry recovers 60%+ of failures that would otherwise score zero.',
  },
  {
    name: 'Champion Promoter',
    icon: '🏅',
    who: 'opencastor-autoresearch CI',
    description: 'When a new config beats the current champion score, it is stored as champion.yaml and written to Firestore as harness_pending for all robots in that tier. Robot owners are notified in the app and can opt in to apply it.',
    what_it_does: 'Distributes the best-known config to the fleet automatically — but never applies it without explicit human consent. P66 safety parameters are stripped from any candidate before promotion.',
  },
]

const IMPACT_LABEL: Record<string, string> = { High: 'High impact', Medium: 'Medium impact', Low: 'Low impact' }

export function OptimizationGuide() {
  return (
    <section style={{ margin: '48px 0' }}>
      <div style={{ marginBottom: 8 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--cyan)', letterSpacing: 1 }}>OPTIMIZATION</span>
      </div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>How to Win: Every Lever Explained</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: 14, maxWidth: 640, marginBottom: 32, lineHeight: 1.7 }}>
        Two sets of controls: what <strong style={{ color: 'var(--text)' }}>you</strong> can change manually, and what the <strong style={{ color: 'var(--text)' }}>system</strong> optimizes automatically. Understanding both is how top robots reach the front of the leaderboard.
      </p>

      {/* Human levers */}
      <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4, color: 'var(--text)' }}>🧑 Human-controlled levers</h3>
      <p style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 18, lineHeight: 1.6 }}>
        These are config values you set yourself, either in the app or in <code style={{ fontFamily: 'var(--font-mono)', color: 'var(--cyan)', fontSize: 12 }}>arm.rcan.yaml</code>.
      </p>
      <div style={{ display: 'grid', gap: 12, marginBottom: 40 }}>
        {HUMAN_LEVERS.map(item => (
          <div key={item.lever} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto', alignItems: 'center', gap: 12, padding: '14px 18px', borderBottom: '1px solid var(--border)' }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 14, color: 'var(--text)' }}>{item.lever}</div>
                <code style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>{item.where}</code>
              </div>
              <div />
              <span style={{ fontSize: 11, padding: '3px 10px', borderRadius: 99, background: `${item.impactColor}18`, color: item.impactColor, fontWeight: 600, whiteSpace: 'nowrap' }}>
                {IMPACT_LABEL[item.impact]}
              </span>
            </div>
            <div style={{ padding: '12px 18px 14px' }}>
              <p style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--text)', marginBottom: 10 }}>{item.description}</p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {item.tips.map((tip, i) => (
                  <div key={i} style={{ fontSize: 12, color: 'var(--text-muted)', paddingLeft: 14, position: 'relative' }}>
                    <span style={{ position: 'absolute', left: 0, color: 'var(--cyan)' }}>›</span>
                    {tip}
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Auto-optimization */}
      <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4, color: 'var(--text)' }}>🤖 System-controlled optimizations</h3>
      <p style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 18, lineHeight: 1.6 }}>
        These run automatically — on your robot or in the research pipeline. You don't configure these directly; they're always on when you're in Community contribute mode.
      </p>
      <div style={{ display: 'grid', gap: 12 }}>
        {AUTO_OPTIMIZATIONS.map(item => (
          <div key={item.name} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, display: 'grid', gridTemplateColumns: '52px 1fr', overflow: 'hidden' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'center', paddingTop: 16, fontSize: 22, background: 'var(--surface2)', borderRight: '1px solid var(--border)' }}>
              {item.icon}
            </div>
            <div style={{ padding: '14px 18px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6, flexWrap: 'wrap', gap: 6 }}>
                <div style={{ fontWeight: 700, fontSize: 14 }}>{item.name}</div>
                <code style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', background: 'var(--surface2)', padding: '2px 8px', borderRadius: 4 }}>{item.who}</code>
              </div>
              <p style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--text)', marginBottom: 8 }}>{item.description}</p>
              <div style={{ padding: '8px 12px', background: 'var(--surface2)', borderRadius: 8, borderLeft: '3px solid var(--cyan)' }}>
                <span style={{ fontSize: 11, color: 'var(--cyan)', fontWeight: 600 }}>What it does: </span>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{item.what_it_does}</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Summary callout */}
      <div style={{ marginTop: 28, padding: '18px 22px', border: '1px solid rgba(85,215,237,0.3)', borderRadius: 12, background: 'rgba(85,215,237,0.04)' }}>
        <div style={{ fontSize: 14, color: 'var(--cyan)', fontWeight: 700, marginBottom: 8 }}>The optimization flywheel</div>
        <p style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.75, marginBottom: 0 }}>
          You control the hardware and model. The system finds the best config for that hardware+model combination. The leaderboard shows who found the best combination. The Research Synthesis panel tells you <em>why</em> that combination won. That insight feeds back into your next hardware decision. This is the loop.
        </p>
        <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8 }}>
          {['Choose hardware', '→ Enable contribute', '→ System evaluates 263K configs', '→ Champion promoted', '→ You apply opt-in', '→ Score improves', '→ Earn credits'].map((step, i) => (
            <div key={i} style={{ fontSize: 11, color: i % 2 === 0 ? 'var(--cyan)' : 'var(--text-muted)', fontFamily: 'var(--font-mono)', textAlign: 'center', padding: '6px 8px', background: 'var(--surface)', borderRadius: 6 }}>
              {step}
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
