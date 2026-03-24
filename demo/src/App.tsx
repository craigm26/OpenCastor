import { useState } from 'react'
import { TIERS, SYNTHESIS_INSIGHTS, TOTAL_RUNS, TOTAL_ROBOTS, SEARCH_SPACE_EXPLORED, CHAMPION_SCORE, type Domain, type LeaderboardEntry } from './data/demo_data'
import { HowItWorks } from './components/HowItWorks'
import { CompetitionFormats } from './components/CompetitionFormats'
import { OptimizationGuide } from './components/OptimizationGuide'
import { BenchmarkTransparency } from './components/BenchmarkTransparency'
import { ConfigLibrary } from './components/ConfigLibrary'
import { RcanV2Proposal } from './components/RcanV2Proposal'

const DOMAIN_LABELS: Record<Domain, string> = { general: '⚙️ General', home: '🏠 Home', industrial: '🏭 Industrial' }

function ScoreBar({ score, max = 1 }: { score: number; max?: number }) {
  const pct = (score / max) * 100
  const color = score > 0.9 ? '#55d7ed' : score > 0.8 ? '#4ade80' : score > 0.7 ? '#ffba38' : '#f87171'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ flex: 1, height: 4, background: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 2, transition: 'width 0.6s ease' }} />
      </div>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color, minWidth: 40, textAlign: 'right' }}>
        {score.toFixed(4)}
      </span>
    </div>
  )
}

function EntryDetail({ entry, onClose }: { entry: LeaderboardEntry; onClose: () => void }) {
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(14,20,22,0.92)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100, padding: 16 }}>
      <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 16, padding: 28, maxWidth: 560, width: '100%', maxHeight: '90vh', overflowY: 'auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--cyan)', letterSpacing: 1, marginBottom: 4 }}>ROBOT PROFILE</div>
            <h2 style={{ fontSize: 18, fontWeight: 700 }}>{entry.id}</h2>
            <div style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 2 }}>{entry.location} · {entry.hardware}</div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: '1px solid var(--border)', color: 'var(--text-muted)', borderRadius: 8, width: 32, height: 32, fontSize: 16 }}>✕</button>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
          {[
            { label: 'OHB-1 Score', value: entry.score.toFixed(4), color: 'var(--cyan)' },
            { label: 'Castor Credits', value: entry.credits.toLocaleString() + ' ◆', color: 'var(--amber)' },
            { label: 'Work Units', value: entry.workUnits.toLocaleString(), color: 'var(--text)' },
            { label: 'Last Run', value: entry.lastRun, color: 'var(--text)' },
          ].map(({ label, value, color }) => (
            <div key={label} style={{ background: 'var(--surface)', borderRadius: 10, padding: '12px 14px' }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>{label}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 600, color }}>{value}</div>
            </div>
          ))}
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', letterSpacing: 1, marginBottom: 8 }}>DOMAIN BREAKDOWN</div>
          {(Object.entries(entry.domainScores) as [Domain, number][]).map(([d, s]) => (
            <div key={d} style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 13 }}>{DOMAIN_LABELS[d]}</span>
              </div>
              <ScoreBar score={s} />
            </div>
          ))}
        </div>

        <div style={{ background: 'var(--surface)', borderRadius: 10, padding: 14, marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>WINNING HARNESS CONFIG</div>
          <code style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--cyan)', display: 'block', lineHeight: 1.8 }}>
            {entry.harnessConfig.split(' / ').map((line, i) => (
              <span key={i} style={{ display: 'block' }}>
                <span style={{ color: 'var(--text-muted)' }}>{'> '}</span>{line}
              </span>
            ))}
          </code>
        </div>

        <a
          href={`https://github.com/craigm26/OpenCastor/blob/main/research/champion.yaml`}
          target="_blank"
          rel="noopener noreferrer"
          style={{ display: 'block', textAlign: 'center', padding: '10px 0', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--cyan)', fontSize: 13 }}
        >
          Download champion.yaml →
        </a>
      </div>
    </div>
  )
}

function LeaderboardRow({ entry, activeDomain, onClick }: { entry: LeaderboardEntry; activeDomain: Domain; onClick: () => void }) {
  const rankColors: Record<number, string> = { 1: '#ffba38', 2: '#94a3b8', 3: '#cd7f32' }
  return (
    <tr
      onClick={onClick}
      style={{ borderTop: '1px solid var(--border)', cursor: 'pointer', transition: 'background 0.15s' }}
      onMouseEnter={e => (e.currentTarget.style.background = 'rgba(85,215,237,0.04)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
    >
      <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', fontWeight: 700, color: rankColors[entry.rank] ?? 'var(--text-muted)', fontSize: 13, width: 40 }}>
        {entry.rank === 1 ? '🏅' : `#${entry.rank}`}
      </td>
      <td style={{ padding: '12px 8px' }}>
        <div style={{ fontWeight: 600, fontSize: 13 }}>{entry.id}</div>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{entry.location}</div>
      </td>
      <td style={{ padding: '12px 8px', minWidth: 140 }}>
        <ScoreBar score={entry.domainScores[activeDomain]} />
      </td>
      <td style={{ padding: '12px 8px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', display: 'none' }} className="model-col">
        {entry.model}
      </td>
      <td style={{ padding: '12px 14px', textAlign: 'right' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--amber)', fontWeight: 600 }}>{entry.credits.toLocaleString()} ◆</div>
        {entry.safetyCertified && (
          <div style={{ fontSize: 10, color: '#4ade80', marginTop: 2 }}>✓ Safety certified</div>
        )}
      </td>
    </tr>
  )
}

function FeedbackSheet({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState<'form' | 'thanks'>('form')
  const [ownership, setOwnership] = useState('')
  const [interests, setInterests] = useState<string[]>([])
  const [openText, setOpenText] = useState('')

  const toggleInterest = (v: string) =>
    setInterests(prev => prev.includes(v) ? prev.filter(x => x !== v) : [...prev, v])

  const buildSummary = () => [
    `Robot ownership: ${ownership || '(not answered)'}`,
    `Interests: ${interests.length ? interests.join(', ') : '(none selected)'}`,
    `Open feedback: ${openText || '(none)'}`,
  ].join('\n')

  const submitPublic = () => {
    // Opens a pre-filled GitHub Issue (public, linked to your account)
    const body = [
      `**Robot ownership:** ${ownership || '(not answered)'}`,
      '',
      `**Interests:** ${interests.length ? interests.join(', ') : '(none selected)'}`,
      '',
      `**Open feedback:** ${openText || '(none)'}`,
      '',
      '---',
      '*Submitted via the OpenCastor demo leaderboard.*',
    ].join('\n')

    const url = new URL('https://github.com/craigm26/opencastor-ops/issues/new')
    url.searchParams.set('title', '[Demo Feedback] Leaderboard probe')
    url.searchParams.set('body', body)
    url.searchParams.set('labels', 'demo-feedback')
    window.open(url.toString(), '_blank')
    setStep('thanks')
  }

  const submitPrivate = () => {
    // mailto: — opens user's email client, goes directly to Craig, no account needed
    const subject = encodeURIComponent('[OpenCastor Demo] Private feedback')
    const body = encodeURIComponent(
      buildSummary() + '\n\n---\nSent via craigm26.github.io/OpenCastor'
    )
    window.open(`mailto:craigm26@gmail.com?subject=${subject}&body=${body}`, '_self')
    setStep('thanks')
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(14,20,22,0.92)', display: 'flex', alignItems: 'flex-end', justifyContent: 'center', zIndex: 100, padding: 16 }}>
      <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: '16px 16px 0 0', padding: '28px 24px 40px', maxWidth: 520, width: '100%' }}>
        {step === 'thanks' ? (
          <div style={{ textAlign: 'center', padding: '20px 0' }}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>🙏</div>
            <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>Thanks — you're shaping OpenCastor</h3>
            <p style={{ color: 'var(--text-muted)', marginBottom: 24, fontSize: 14 }}>Your feedback directly informs the real leaderboard build.</p>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
              <a href="https://github.com/craigm26/OpenCastor" target="_blank" rel="noopener noreferrer" style={{ padding: '9px 18px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text)', fontSize: 13 }}>⭐ Star on GitHub</a>
              <a href="https://discord.gg/jMjA8B26Bq" target="_blank" rel="noopener noreferrer" style={{ padding: '9px 18px', background: 'var(--cyan-dim)', border: '1px solid var(--cyan)', borderRadius: 8, color: 'var(--cyan)', fontSize: 13 }}>Join Discord</a>
            </div>
            <button onClick={onClose} style={{ marginTop: 20, background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: 13 }}>Close</button>
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20 }}>
              <h3 style={{ fontSize: 16, fontWeight: 700 }}>Quick feedback (30 sec)</h3>
              <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: 18 }}>✕</button>
            </div>

            <div style={{ marginBottom: 18 }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Do you have a robot / Pi?</div>
              {['Yes — it runs OpenCastor', 'Yes — but not running OpenCastor yet', 'Not yet — but I want to', 'No — just exploring'].map(opt => (
                <label key={opt} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, cursor: 'pointer', fontSize: 13 }}>
                  <input type="radio" name="ownership" value={opt} checked={ownership === opt} onChange={() => setOwnership(opt)}
                    style={{ accentColor: 'var(--cyan)' }} />
                  {opt}
                </label>
              ))}
            </div>

            <div style={{ marginBottom: 18 }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>What's most interesting to you? (pick all)</div>
              {['Benchmark scores', 'Earning credits for compute', 'Safety certification badge', 'Downloading winning harness configs', 'Profit-sharing at Diamond tier', 'Hardware tier competition', 'Research synthesis insights'].map(opt => (
                <label key={opt} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, cursor: 'pointer', fontSize: 13 }}>
                  <input type="checkbox" checked={interests.includes(opt)} onChange={() => toggleInterest(opt)}
                    style={{ accentColor: 'var(--cyan)' }} />
                  {opt}
                </label>
              ))}
            </div>

            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Anything confusing or missing?</div>
              <textarea
                value={openText}
                onChange={e => setOpenText(e.target.value)}
                rows={3}
                placeholder="Your thoughts..."
                style={{ width: '100%', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', color: 'var(--text)', fontSize: 13, resize: 'vertical', fontFamily: 'var(--font-body)' }}
              />
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <button
                onClick={submitPrivate}
                style={{ width: '100%', padding: '11px 0', background: 'var(--cyan)', color: '#0e1416', borderRadius: 8, border: 'none', fontWeight: 700, fontSize: 14, fontFamily: 'var(--font-head)', cursor: 'pointer' }}
              >
                ✉️ Send privately — craigm26@gmail.com
              </button>
              <button
                onClick={submitPublic}
                style={{ width: '100%', padding: '10px 0', background: 'transparent', color: 'var(--text-muted)', borderRadius: 8, border: '1px solid var(--border)', fontWeight: 600, fontSize: 13, fontFamily: 'var(--font-head)', cursor: 'pointer' }}
              >
                Post as public GitHub issue
              </button>
              <p style={{ fontSize: 11, color: 'var(--text-muted)', textAlign: 'center', margin: 0 }}>
                Private feedback opens your email client. No account needed.
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function ResearchSynthesis() {
  const domainColors: Record<Domain, string> = { general: 'var(--cyan)', home: 'var(--amber)', industrial: '#c084fc' }
  const confidenceColor = { high: '#4ade80', medium: 'var(--amber)', emerging: 'var(--text-muted)' }

  return (
    <section style={{ margin: '40px 0' }}>
      <div style={{ marginBottom: 8 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--cyan)', letterSpacing: 1 }}>META-ANALYSIS</span>
      </div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Research Synthesis</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: 14, maxWidth: 600, marginBottom: 24, lineHeight: 1.7 }}>
        What kind of autoresearching is actually working — and in which domains? These are signals distilled from {(7341).toLocaleString()} benchmark runs across {127} robots. This is the layer above the leaderboard.
      </p>

      <div style={{ display: 'grid', gap: 16 }}>
        {SYNTHESIS_INSIGHTS.map(insight => (
          <div key={insight.domain} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontFamily: 'var(--font-head)', fontWeight: 700, fontSize: 14, color: domainColors[insight.domain] }}>
                  {DOMAIN_LABELS[insight.domain]}
                </span>
                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 99, background: 'rgba(255,255,255,0.06)', color: 'var(--text-muted)' }}>
                  n={insight.dataPoints.toLocaleString()} runs
                </span>
              </div>
              <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 99, background: 'rgba(255,255,255,0.06)', color: confidenceColor[insight.confidence] }}>
                {insight.confidence} confidence
              </span>
            </div>
            <p style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--text)', marginBottom: 14 }}>{insight.finding}</p>
            <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: '10px 14px' }}>
              <span style={{ fontSize: 11, color: 'var(--text-muted)', marginRight: 8 }}>Winning pattern:</span>
              <code style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: domainColors[insight.domain] }}>{insight.winningPattern}</code>
            </div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 20, padding: '16px 20px', border: '1px solid rgba(255,186,56,0.3)', borderRadius: 12, background: 'rgba(255,186,56,0.05)' }}>
        <div style={{ fontSize: 13, color: 'var(--amber)', fontWeight: 600, marginBottom: 6 }}>💡 The Karpathy insight</div>
        <p style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.7 }}>
          "I see a lot of people autoresearching. But I don't see as many people trying to go up a step, and synthesize signals about what kind of autoresearching is good, in which domains." — this panel is that step up. The leaderboard shows <em>who's winning</em>. The synthesis shows <em>why</em> and what to try next.
        </p>
      </div>
    </section>
  )
}

export default function App() {
  const [activeTier, setActiveTier] = useState(0)
  const [activeDomain, setActiveDomain] = useState<Domain>('general')
  const [selectedEntry, setSelectedEntry] = useState<LeaderboardEntry | null>(null)
  const [showFeedback, setShowFeedback] = useState(false)

  const tier = TIERS[activeTier]

  return (
    <div style={{ minHeight: '100vh', background: 'var(--obsidian)' }}>
      {/* Demo amber banner */}
      <div style={{ background: 'rgba(255,186,56,0.12)', borderBottom: '1px solid rgba(255,186,56,0.25)', padding: '10px 20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <span style={{ fontSize: 13, color: 'var(--amber)' }}>
          ⚠️ <strong>Demo data</strong> — not real runs. Seeded to show what the real leaderboard will look like.
        </span>
        <button
          onClick={() => setShowFeedback(true)}
          style={{ background: 'var(--amber)', color: '#0e1416', border: 'none', borderRadius: 6, padding: '5px 14px', fontSize: 12, fontWeight: 700, fontFamily: 'var(--font-head)' }}
        >
          Share feedback →
        </button>
      </div>

      <div style={{ maxWidth: 860, margin: '0 auto', padding: '32px 20px 80px' }}>
        {/* Header */}
        <header style={{ marginBottom: 40 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
            <span style={{ fontSize: 28 }}>🤖</span>
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--cyan)', letterSpacing: 1.5, marginBottom: 2 }}>OPENCASTOR</div>
              <h1 style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.1 }}>Harness Research Leaderboard</h1>
            </div>
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: 14, maxWidth: 580, lineHeight: 1.7, marginBottom: 20 }}>
            Which robot AI harness config wins on your hardware? Open benchmark, fleet-wide data, community-driven. Every robot earns Castor Credits for contributing evaluation runs.
          </p>

          {/* Stats bar */}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {[
              { label: 'Benchmark runs', value: TOTAL_RUNS.toLocaleString() },
              { label: 'Robots in fleet', value: TOTAL_ROBOTS.toString() },
              { label: 'Search space explored', value: `${SEARCH_SPACE_EXPLORED}%` },
              { label: 'Best OHB-1 score', value: CHAMPION_SCORE.toFixed(4), color: 'var(--cyan)' },
            ].map(({ label, value, color }) => (
              <div key={label} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '10px 16px', flex: '1 1 120px' }}>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>{label}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 16, color: color ?? 'var(--text)' }}>{value}</div>
              </div>
            ))}
          </div>
        </header>

        {/* Hardware tier tabs */}
        <div style={{ display: 'flex', gap: 8, overflowX: 'auto', marginBottom: 24, paddingBottom: 4 }}>
          {TIERS.map((t, i) => (
            <button
              key={t.id}
              onClick={() => setActiveTier(i)}
              style={{
                padding: '8px 14px',
                borderRadius: 8,
                border: `1px solid ${activeTier === i ? 'var(--cyan)' : 'var(--border)'}`,
                background: activeTier === i ? 'var(--cyan-dim)' : 'var(--surface)',
                color: activeTier === i ? 'var(--cyan)' : 'var(--text)',
                fontFamily: 'var(--font-head)',
                fontWeight: activeTier === i ? 700 : 400,
                fontSize: 13,
                whiteSpace: 'nowrap',
                transition: 'all 0.15s',
              }}
            >
              {t.icon} {t.label}
            </button>
          ))}
        </div>

        {/* Tier leaderboard */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, overflow: 'hidden', marginBottom: 32 }}>
          <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
            <div>
              <h2 style={{ fontSize: 16, fontWeight: 700 }}>{tier.icon} {tier.label}</h2>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>{tier.subtitle}</div>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              {(Object.keys(DOMAIN_LABELS) as Domain[]).map(d => (
                <button
                  key={d}
                  onClick={() => setActiveDomain(d)}
                  style={{
                    padding: '5px 11px',
                    borderRadius: 6,
                    border: `1px solid ${activeDomain === d ? 'var(--cyan)' : 'var(--border)'}`,
                    background: activeDomain === d ? 'var(--cyan-dim)' : 'transparent',
                    color: activeDomain === d ? 'var(--cyan)' : 'var(--text-muted)',
                    fontSize: 11,
                    fontFamily: 'var(--font-body)',
                    transition: 'all 0.15s',
                  }}
                >
                  {DOMAIN_LABELS[d]}
                </button>
              ))}
            </div>
          </div>

          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                <th style={{ padding: '10px 14px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>#</th>
                <th style={{ padding: '10px 8px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>Robot</th>
                <th style={{ padding: '10px 8px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>OHB-1 Score</th>
                <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 11, color: 'var(--text-muted)', fontWeight: 500 }}>Credits</th>
              </tr>
            </thead>
            <tbody>
              {tier.entries.map(entry => (
                <LeaderboardRow key={entry.id} entry={entry} activeDomain={activeDomain} onClick={() => setSelectedEntry(entry)} />
              ))}
            </tbody>
          </table>

          <div style={{ padding: '12px 20px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Click any row to see harness config → tap "Download champion.yaml"</span>
            <a href="https://opencastor.com/docs/contribute" target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: 'var(--cyan)' }}>Contribute your robot →</a>
          </div>
        </div>

        <ConfigLibrary />

        <CompetitionFormats />

        <HowItWorks />

        <OptimizationGuide />

        <BenchmarkTransparency />

        <ResearchSynthesis />

        <RcanV2Proposal />

        {/* CTA */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, padding: 28, textAlign: 'center' }}>
          <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>Run your robot in the real benchmark</h3>
          <p style={{ color: 'var(--text-muted)', fontSize: 14, maxWidth: 480, margin: '0 auto 20px', lineHeight: 1.7 }}>
            Install OpenCastor on your Pi, enable <code style={{ fontFamily: 'var(--font-mono)', color: 'var(--cyan)', fontSize: 12 }}>agent.contribute</code>, and your idle compute earns Castor Credits toward profit sharing.
          </p>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
            <a href="https://github.com/craigm26/OpenCastor" target="_blank" rel="noopener noreferrer"
              style={{ padding: '10px 22px', background: 'var(--cyan)', color: '#0e1416', borderRadius: 8, fontWeight: 700, fontSize: 14, fontFamily: 'var(--font-head)' }}>
              ⭐ Star on GitHub
            </a>
            <a href="https://docs.opencastor.com/runtime/contribute/" target="_blank" rel="noopener noreferrer"
              style={{ padding: '10px 22px', background: 'var(--surface2)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 8, fontSize: 14, fontFamily: 'var(--font-head)' }}>
              Read the docs →
            </a>
            <button
              onClick={() => setShowFeedback(true)}
              style={{ padding: '10px 22px', background: 'var(--amber-dim)', border: '1px solid var(--amber)', color: 'var(--amber)', borderRadius: 8, fontSize: 14, fontFamily: 'var(--font-head)' }}
            >
              Share feedback
            </button>
          </div>
        </div>

        <footer style={{ marginTop: 48, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>
          <p>OpenCastor · <a href="https://opencastor.com">opencastor.com</a> · <a href="https://github.com/craigm26/OpenCastor">GitHub</a> · <a href="https://discord.gg/jMjA8B26Bq">Discord</a></p>
          <p style={{ marginTop: 6 }}>Demo data · not real runs · <a href="https://docs.opencastor.com/research/ohb1-benchmark/">OHB-1 benchmark spec</a> · <a href="https://github.com/craigm26/OpenCastor/blob/main/research/leaderboard.csv">View raw CSV ↗</a></p>
        </footer>
      </div>

      {selectedEntry && <EntryDetail entry={selectedEntry} onClose={() => setSelectedEntry(null)} />}
      {showFeedback && <FeedbackSheet onClose={() => setShowFeedback(false)} />}
    </div>
  )
}
