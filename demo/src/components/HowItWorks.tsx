const STEPS = [
  {
    number: '01',
    title: 'What is Autoresearch?',
    color: '#55d7ed',
    body: `Autoresearch is using AI to optimize AI — running systematic experiments across a large configuration space to find which settings produce the best real-world results, instead of guessing or manually tuning.

For robot AI, the "configuration space" includes: which model to use, how much thinking budget to allocate, how large a context window to maintain, whether to enable drift detection, retry-on-error logic, memory management, and more. There are 263,424 possible combinations across 9 dimensions.

No human can evaluate all of them. An automated research pipeline can.`,
    detail: '263,424 possible harness configs across 9 dimensions: model family × thinking budget × context budget × max iterations × drift detection × retry logic × pattern engine × memory strategy × security level',
  },
  {
    number: '02',
    title: 'What is opencastor-autoresearch?',
    color: '#55d7ed',
    body: `opencastor-autoresearch is the specific module that runs harness experiments for OpenCastor robots. It lives at github.com/craigm26/opencastor-autoresearch and does four things:

1. Generates candidate harness configs from the search space
2. Evaluates each config against the OHB-1 benchmark (30 real robot tasks)
3. Scores results across three domains: General reasoning, Home automation, Industrial
4. Promotes the winning config to champion.yaml — the default config shipped with OpenCastor

Every evaluation run is tracked with the Robot Registration Number (RRN) of the robot that ran it. This is the lineage system: your robot gets credit for every config it tests.`,
    detail: 'Current champion: lower_cost config with thinking_budget=4096, context_budget=32768, drift_detection=true — OHB-1 score 0.9801 on Server tier, 0.9241 on Pi5+Hailo8L.',
  },
  {
    number: '03',
    title: 'What is OHB-1?',
    color: '#ffba38',
    body: `OHB-1 (OpenCastor Harness Benchmark, version 1) is the evaluation benchmark. It runs 30 real robot tasks against a candidate harness config and scores the results.

Tasks are grouped into three domains:
• General (10 tasks): reasoning, planning, multi-step coordination
• Home (10 tasks): object handover, schedule reading, appliance control
• Industrial (10 tasks): anomaly reporting, multi-robot coordination, sensor alerts

Each task is scored on a rubric: did the model call the right tools? Did it produce a grippable output? Did it respect P66 (physical consent) requirements? Did it trigger alerts when needed?

OHB-1 uses gemma3:1b via Ollama — a local model that runs on any Pi. No API keys required, no cloud cost, fully reproducible.`,
    detail: 'Known failure modes in current champion: home_handover_cup (missing p66_consent + grip call), industrial_sensor_alert (missing alert() call), complex multi-step tasks that timeout at 30s.',
  },
  {
    number: '04',
    title: 'What does the Leaderboard show?',
    color: '#55d7ed',
    body: `The leaderboard answers: which harness config wins on which hardware?

Hardware matters because a Pi5 4GB with a local gemma3:1b model faces different constraints than a cloud server running Claude Sonnet. The winning config on Server (high thinking budget, large context) would be catastrophically slow on a Pi Zero. The leaderboard is segmented by tier so comparisons are fair.

Each entry shows:
• OHB-1 score (overall + per-domain breakdown)
• The exact harness config that produced it
• Which robot submitted the evaluation (RRN-based lineage)
• Castor Credits earned for contributing

The Research Synthesis panel goes one level up: instead of showing who is winning, it shows why — which config dimensions correlate with score improvement in each domain.`,
    detail: 'Key insight: retry_on_error=true is the single flag most correlated with industrial score improvement (+12% median). For home tasks, local models with low latency beat cloud models despite lower raw scores.',
  },
  {
    number: '05',
    title: 'How does the winning config get used?',
    color: '#4ade80',
    body: `When a config wins a competition, it becomes the new champion. Here's the pipeline:

1. Champion stored in champion.yaml in the opencastor-autoresearch repo
2. Champion also written to Firestore as harness_pending for each connected robot
3. Robot owners see an amber banner in the OpenCastor app: "New champion config available — apply to this robot?"
4. Owner taps "Apply" → config is loaded on next restart
5. The champion config is never auto-deployed. It is always opt-in.

The P66 safety invariant is preserved: champion configs cannot disable physical consent requirements, modify ESTOP logic, or change motor parameters. These are stripped before any champion config is applied.

For competitions (Sprint, Threshold Race, Bracket Season): the winner's config is promoted as the new tier champion. Other robots in that tier see it as a pending update and can opt in.`,
    detail: 'P66 guarantee: apply-champion endpoint pops p66_audit from any incoming config before writing — the safety layer cannot be changed by the research pipeline.',
  },
  {
    number: '06',
    title: 'How do Castor Credits work?',
    color: '#ffba38',
    body: `Every evaluation run your robot completes earns Castor Credits. Credits are a contribution ledger — they track how much of the search space your robot has explored.

Credits are earned for:
• Completing a work unit (1 harness config evaluated against OHB-1)
• Contributing a result that becomes a new tier champion (bonus multiplier)
• Running in community mode vs personal mode (community runs are public and credit-eligible)

Credits are not currently redeemable for cash — they're a credibility and priority signal. In Phase 2, Diamond-tier contributors (≥5,000 credits) will be eligible for profit sharing from enterprise API revenue.

The goal: create a flywheel where robot owners have an economic incentive to leave their robots running evaluations overnight, which improves the benchmark for everyone.`,
    detail: 'Current top contributor: CloudFleet_NYC with 4,102 credits (612 work units). Pi-class top: Fleet_Pi5_Berlin with 2,847 credits (441 work units).',
  },
]

export function HowItWorks() {
  return (
    <section style={{ margin: '48px 0' }}>
      <div style={{ marginBottom: 8 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--cyan)', letterSpacing: 1 }}>EXPLAINER</span>
      </div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>How It Works</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: 14, maxWidth: 620, marginBottom: 32, lineHeight: 1.7 }}>
        From idle robot to research contribution to winning harness config — the full pipeline explained.
      </p>

      <div style={{ display: 'grid', gap: 20 }}>
        {STEPS.map((step) => (
          <StepCard key={step.number} step={step} />
        ))}
      </div>
    </section>
  )
}

function StepCard({ step }: { step: typeof STEPS[0] }) {
  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 14,
      overflow: 'hidden',
      display: 'grid',
      gridTemplateColumns: '80px 1fr',
    }}>
      {/* Step number sidebar */}
      <div style={{
        background: `linear-gradient(180deg, ${step.color}18 0%, transparent 100%)`,
        borderRight: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        paddingTop: 22,
      }}>
        <span style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 22,
          fontWeight: 700,
          color: step.color,
          opacity: 0.7,
        }}>{step.number}</span>
      </div>

      {/* Content */}
      <div style={{ padding: '20px 22px' }}>
        <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 14, color: 'var(--text)' }}>
          {step.title}
        </h3>

        {step.body.split('\n\n').map((para, i) => (
          <p key={i} style={{
            fontSize: 13,
            lineHeight: 1.75,
            color: para.startsWith('•') ? 'var(--text)' : 'var(--text)',
            marginBottom: 10,
            whiteSpace: 'pre-line',
          }}>
            {para}
          </p>
        ))}

        {/* Detail chip */}
        <div style={{
          marginTop: 14,
          padding: '9px 14px',
          background: 'var(--surface2)',
          borderRadius: 8,
          borderLeft: `3px solid ${step.color}`,
        }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            {step.detail}
          </span>
        </div>
      </div>
    </div>
  )
}
