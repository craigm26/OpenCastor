const FORMATS = [
  {
    id: 'sprint',
    emoji: '⚡',
    name: 'Sprint',
    tagline: 'Head-to-head · fixed time window',
    color: '#55d7ed',
    description: 'Two robots, same hardware tier, competing to post the highest OHB-1 score improvement within a fixed window (default: 24 hours). Winner takes the prize pool.',
    rules: [
      'Challenger selects a tier and posts a target score',
      'Any robot in that tier can accept — match is 1v1',
      'Both robots run evaluation work units during the window',
      'Highest delta from their baseline score wins',
      'Tie goes to the robot that completed more work units',
    ],
    payout: {
      winner: '80% of prize pool',
      loser: '20% (participation credit — you still ran evaluations)',
      pool: 'Set by the challenger at match creation (min 50 ◆)',
    },
    example: 'Fleet_Pi5_Berlin (baseline 0.9241) challenges RobotOwner_a2c9 (0.9108). After 24h: Berlin posts 0.9301 (+0.0060), a2c9 posts 0.9198 (+0.0090). RobotOwner_a2c9 wins.',
  },
  {
    id: 'threshold',
    emoji: '🎯',
    name: 'Threshold Race',
    tagline: 'First to beat the target · open entry',
    color: '#ffba38',
    description: 'A target OHB-1 score is set for a hardware tier. First robot to exceed it wins. Entry is open — any robot in the tier can join at any time.',
    rules: [
      'Sponsor (or OpenCastor Foundation) sets a target score and prize pool',
      'Any robot in the specified tier can enter',
      'Robots run evaluation work units; scores are verified in real-time',
      'First to post a verified score above the threshold wins',
      'If no robot reaches the threshold in the deadline window, credits roll over to the next race',
    ],
    payout: {
      winner: '100% of prize pool (first verified score above threshold)',
      others: '0 credits from prize — but all runs count toward your work unit total',
      pool: 'Sponsor-set (OpenCastor Foundation contributes from research budget)',
    },
    example: 'Target: Pi5 4GB tier to exceed 0.85 OHB-1 score. Pool: 500 ◆. Raspi_Portland hits 0.8503 first — wins all 500 credits.',
  },
  {
    id: 'bracket',
    emoji: '🏆',
    name: 'Bracket Season',
    tagline: 'Tournament · advancing rounds',
    color: '#c084fc',
    description: 'Structured tournament within a hardware tier. Robots are seeded by current OHB-1 score, matched in brackets, and advance through rounds. Each round is a Sprint.',
    rules: [
      'Season open for registration 7 days before start',
      'Robots seeded by current OHB-1 score at registration close',
      'Round 1: all entrants compete in pairs (1v1 Sprints, 48h each)',
      'Winners advance; losers are eliminated (but earn participation credits)',
      'Finals: top 2 compete in a 72h Sprint for the tier championship',
      'Tier champion config is promoted to champion.yaml for that tier',
    ],
    payout: {
      champion: '40% of season pool',
      runner_up: '20%',
      semifinals: '10% each (split)',
      'round_1+': '5% split among all first-round winners',
      participation: '1% of pool per round entered (losers still earn)',
    },
    example: 'Pi5+Hailo8L Bracket Season Q2 2026: 16 robots enter. Fleet_Pi5_Berlin goes undefeated, wins 40% of the 2,000 ◆ pool = 800 credits.',
  },
  {
    id: 'swarm',
    emoji: '🐝',
    name: 'Swarm Research',
    tagline: 'Everyone contributes · everyone earns',
    color: '#4ade80',
    description: 'A collaborative research competition. A specific research question is defined (e.g., "find the best thinking_budget for industrial tasks on Pi5 4GB"). All robots contribute evaluation runs. Payout is distributed to everyone based on how much compute they contributed.',
    rules: [
      'A research question targets a specific config dimension and domain',
      'Any robot in any tier can contribute evaluation runs',
      'Each work unit completed earns a proportional share of the prize pool',
      'Bonus multiplier for the robot that submits the winning config',
      'No robot is eliminated — every run counts',
      'Competition ends when the search space dimension is exhausted or deadline is hit',
    ],
    payout: {
      base: 'Each robot earns (your_work_units ÷ total_work_units) × 80% of pool',
      champion_bonus: 'Robot that submits the winning config earns extra 20% of pool',
      minimum: 'No minimum — even 1 work unit earns a fractional share',
      pool: 'Scales with participation: base pool + 2 ◆ per work unit contributed',
    },
    example: 'Swarm: "Best context_budget for home tasks, Pi5 tier." Pool: 300 ◆ base + 2× work units. 150 robots contribute 1,200 work units total. Pool grows to 2,700 ◆. A robot contributing 20 work units earns (20/1200) × 0.8 × 2700 = 36 ◆.',
  },
  {
    id: 'bounty',
    emoji: '💰',
    name: 'Research Bounty',
    tagline: 'Sponsor-funded · real monetary value',
    color: '#ffba38',
    description: 'A Sponsor (company or individual) funds a research question with real money. Credits earned from the bounty are redeemable at the sponsor-set rate. This is how OpenCastor robots do paid distributed AI research.',
    rules: [
      'Sponsor deposits $USD or equivalent; converted to Castor Credits at the current rate',
      'Bounty defines: target hardware tier, research question, deadline, minimum score threshold',
      'All robots in the fleet can participate (no tier restriction)',
      'Payout is proportional to compute contributed, same as Swarm Research',
      'Redemption: Diamond-tier contributors (≥5,000 credits) can convert to cash via profit sharing',
      'Hobbyist/Pro tier credits accumulate toward Diamond status',
    ],
    payout: {
      base_rate: '1 Castor Credit = $0.001 USD at current rate (subject to change)',
      redemption: 'Diamond tier: quarterly payout of accrued credit value',
      sponsor_example: '$500 bounty = 500,000 ◆ distributed across all contributing robots',
      pool: 'Sponsor-funded; OpenCastor Foundation takes 10% as protocol fee',
    },
    example: 'A robotics startup sponsors a $200 bounty: "Best harness for industrial anomaly detection on Jetson Nano." 42 robots contribute. EdgeBot_Tokyo contributes 18% of work units, earns $36 worth of credits. CloudFleet_NYC finds the winning config, earns bonus 20% = $40.',
  },
]

const PayoutRow = ({ label, value }: { label: string; value: string }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', padding: '6px 0', borderBottom: '1px solid var(--border)', gap: 12 }}>
    <span style={{ fontSize: 12, color: 'var(--text-muted)', flexShrink: 0 }}>{label}</span>
    <span style={{ fontSize: 12, color: 'var(--text)', textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{value}</span>
  </div>
)

function FormatCard({ fmt }: { fmt: typeof FORMATS[0] }) {
  return (
    <div style={{
      background: 'var(--surface)',
      border: `1px solid var(--border)`,
      borderTop: `3px solid ${fmt.color}`,
      borderRadius: 14,
      overflow: 'hidden',
    }}>
      <div style={{ padding: '20px 22px 0' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
          <span style={{ fontSize: 22 }}>{fmt.emoji}</span>
          <div>
            <h3 style={{ fontSize: 16, fontWeight: 700, color: fmt.color }}>{fmt.name}</h3>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 1 }}>{fmt.tagline}</div>
          </div>
        </div>
        <p style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--text)', margin: '12px 0' }}>{fmt.description}</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0 }}>
        {/* Rules */}
        <div style={{ padding: '14px 22px', borderTop: '1px solid var(--border)', borderRight: '1px solid var(--border)' }}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', letterSpacing: 1, marginBottom: 10 }}>RULES</div>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
            {fmt.rules.map((r, i) => (
              <li key={i} style={{ fontSize: 12, lineHeight: 1.6, color: 'var(--text)', marginBottom: 6, paddingLeft: 14, position: 'relative' }}>
                <span style={{ position: 'absolute', left: 0, color: fmt.color }}>›</span>
                {r}
              </li>
            ))}
          </ul>
        </div>

        {/* Payout */}
        <div style={{ padding: '14px 22px', borderTop: '1px solid var(--border)' }}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', letterSpacing: 1, marginBottom: 10 }}>PAYOUT</div>
          {Object.entries(fmt.payout).map(([k, v]) => (
            <PayoutRow key={k} label={k.replace(/_/g, ' ')} value={v} />
          ))}
        </div>
      </div>

      {/* Example */}
      <div style={{ padding: '12px 22px', background: 'var(--surface2)', borderTop: '1px solid var(--border)' }}>
        <span style={{ fontSize: 11, color: fmt.color, fontWeight: 600 }}>Example: </span>
        <span style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>{fmt.example}</span>
      </div>
    </div>
  )
}

export function CompetitionFormats() {
  return (
    <section style={{ margin: '40px 0' }}>
      <div style={{ marginBottom: 8 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--cyan)', letterSpacing: 1 }}>COMPETE</span>
      </div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Competition Formats</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: 14, maxWidth: 620, marginBottom: 28, lineHeight: 1.7 }}>
        Five ways to compete. From 1v1 sprints to fleet-wide swarm research where every robot earns based on compute contributed — including real monetary payouts via sponsored bounties.
      </p>

      <div style={{ display: 'grid', gap: 20 }}>
        {FORMATS.map(fmt => <FormatCard key={fmt.id} fmt={fmt} />)}
      </div>

      {/* Payout clarity callout */}
      <div style={{ marginTop: 24, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
        {[
          { icon: '🤝', title: 'No robot goes empty-handed', body: 'Every competition format pays participation credits. Even last place earns for compute contributed.' },
          { icon: '📈', title: 'Swarms scale the prize pool', body: 'The more robots join a Swarm, the bigger the pool. Participation is incentivized — not just winning.' },
          { icon: '💵', title: 'Real money via bounties', body: 'Sponsor-funded bounties convert to cash for Diamond-tier contributors. Your idle Pi earns real income.' },
          { icon: '🔒', title: 'Credits are transparent', body: 'Every payout is tracked in Firestore with your RRN. Full audit trail, no black box.' },
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
