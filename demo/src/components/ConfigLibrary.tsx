// ConfigLibrary.tsx
// Shareable, downloadable harness configs.
// Every config is a .yaml file in github.com/craigm26/OpenCastor/research/presets/
// Machine-readable index at research/index.json

const INDEX_URL = 'https://raw.githubusercontent.com/craigm26/OpenCastor/main/research/index.json'

interface ConfigEntry {
  id: string
  name: string
  description: string
  yaml_url: string
  target_hardware: string[]
  best_for: string[]
  ohb1_score: number
  is_champion: boolean
  tags: string[]
}

// Embedded so the demo works without a network fetch (raw.githubusercontent.com CORS varies)
const CONFIGS: ConfigEntry[] = [
  {
    id: 'lower_cost',
    name: 'Lower Cost',
    description: 'Best overall balance of quality vs. cost. OHB-1 champion. Works on all hardware tiers. The default config shipped with OpenCastor.',
    yaml_url: 'https://raw.githubusercontent.com/craigm26/OpenCastor/main/research/presets/lower_cost.yaml',
    target_hardware: ['pi5_hailo', 'pi5_8gb', 'pi5_4gb', 'jetson', 'server', 'waveshare'],
    best_for: ['general', 'home', 'industrial'],
    ohb1_score: 0.6541,
    is_champion: true,
    tags: ['recommended', 'all-hardware', 'cost-efficient'],
  },
  {
    id: 'quality_first',
    name: 'Quality First',
    description: 'Maximizes OHB-1 score. Large thinking and context budgets. Server or Pi5+Hailo8L with cloud model access.',
    yaml_url: 'https://raw.githubusercontent.com/craigm26/OpenCastor/main/research/presets/quality_first.yaml',
    target_hardware: ['server', 'pi5_hailo'],
    best_for: ['industrial', 'general'],
    ohb1_score: 0.9801,
    is_champion: false,
    tags: ['high-performance', 'cloud', 'server'],
  },
  {
    id: 'local_only',
    name: 'Local Only',
    description: 'Fully offline. Zero cloud API calls. gemma3:1b via Ollama. For privacy-sensitive deployments or offline environments.',
    yaml_url: 'https://raw.githubusercontent.com/craigm26/OpenCastor/main/research/presets/local_only.yaml',
    target_hardware: ['pi5_4gb', 'pi5_8gb', 'jetson', 'waveshare'],
    best_for: ['home'],
    ohb1_score: 0.8103,
    is_champion: false,
    tags: ['offline', 'privacy', 'no-api-key'],
  },
  {
    id: 'industrial_optimized',
    name: 'Industrial Optimized',
    description: 'Retry-heavy, alert-aware, high context. retry_on_error=true is the single flag most correlated with industrial improvement (+12% median).',
    yaml_url: 'https://raw.githubusercontent.com/craigm26/OpenCastor/main/research/presets/industrial_optimized.yaml',
    target_hardware: ['server', 'pi5_hailo', 'pi5_8gb'],
    best_for: ['industrial'],
    ohb1_score: 0.8812,
    is_champion: false,
    tags: ['industrial', 'retry', 'high-context'],
  },
  {
    id: 'home_optimized',
    name: 'Home Optimized',
    description: 'Low-latency local model, strict P66. Sub-second grip calls for object handover. Home automation tasks need latency, not raw intelligence.',
    yaml_url: 'https://raw.githubusercontent.com/craigm26/OpenCastor/main/research/presets/home_optimized.yaml',
    target_hardware: ['pi5_hailo', 'pi5_8gb', 'pi5_4gb', 'waveshare'],
    best_for: ['home'],
    ohb1_score: 0.8644,
    is_champion: false,
    tags: ['home', 'low-latency', 'local'],
  },
]

const HW_LABELS: Record<string, string> = {
  pi5_hailo: 'Pi5+Hailo8L',
  pi5_8gb: 'Pi5 8GB',
  pi5_4gb: 'Pi5 4GB',
  jetson: 'Jetson Nano',
  server: 'Server',
  waveshare: 'WaveShare',
}

const DOMAIN_COLORS: Record<string, string> = {
  general: '#55d7ed',
  home: '#ffba38',
  industrial: '#c084fc',
}

const CLI_COMMANDS: Record<string, string[]> = {
  single_robot: [
    '# Download and apply to your robot',
    'castor harness apply --url https://raw.githubusercontent.com/craigm26/OpenCastor/main/research/presets/lower_cost.yaml',
    '',
    '# Or apply by config ID',
    'castor harness apply --config lower_cost',
    '',
    '# Verify the applied config',
    'castor harness status',
  ],
  swarm: [
    '# Apply to all robots in your fleet',
    'castor harness broadcast --config lower_cost --fleet all',
    '',
    '# Apply to a hardware tier',
    'castor harness broadcast --config industrial_optimized --tier pi5_hailo',
    '',
    '# Dry-run first (shows what would change)',
    'castor harness broadcast --config lower_cost --fleet all --dry-run',
  ],
  app: [
    '# In the OpenCastor app:',
    '# Settings → Harness → Config Library',
    '# → Select a config → "Preview"',
    '# → "Apply to this robot" (single)',
    '# → "Apply to fleet" (swarm, Pro tier)',
    '',
    '# Deep link (opens directly to config):',
    'opencastor://harness/config/lower_cost',
  ],
}

function downloadYaml(config: ConfigEntry) {
  // Fetch and trigger download — graceful fallback to opening the URL
  fetch(config.yaml_url)
    .then(r => r.text())
    .then(text => {
      const blob = new Blob([text], { type: 'text/yaml' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${config.id}.yaml`
      a.click()
      URL.revokeObjectURL(url)
    })
    .catch(() => window.open(config.yaml_url, '_blank'))
}

function ConfigCard({ config }: { config: ConfigEntry }) {
  const scoreColor = config.ohb1_score > 0.9 ? '#55d7ed' : config.ohb1_score > 0.8 ? '#4ade80' : '#ffba38'

  return (
    <div style={{
      background: 'var(--surface)',
      border: config.is_champion ? '1px solid rgba(85,215,237,0.4)' : '1px solid var(--border)',
      borderRadius: 14,
      overflow: 'hidden',
      position: 'relative',
    }}>
      {config.is_champion && (
        <div style={{ position: 'absolute', top: 0, right: 0, background: 'var(--cyan)', color: '#0e1416', fontSize: 10, fontWeight: 700, padding: '3px 10px', borderRadius: '0 14px 0 8px' }}>
          ★ CHAMPION
        </div>
      )}

      <div style={{ padding: '18px 20px 14px' }}>
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
          <div>
            <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 3 }}>{config.name}</h3>
            <code style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>{config.id}.yaml</code>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 700, color: scoreColor }}>{config.ohb1_score.toFixed(4)}</div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>OHB-1 score</div>
          </div>
        </div>

        <p style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.65, marginBottom: 12 }}>{config.description}</p>

        {/* Domain badges */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
          {config.best_for.map(d => (
            <span key={d} style={{ fontSize: 11, padding: '2px 9px', borderRadius: 99, background: `${DOMAIN_COLORS[d]}18`, color: DOMAIN_COLORS[d] }}>
              {d === 'general' ? '⚙️' : d === 'home' ? '🏠' : '🏭'} {d}
            </span>
          ))}
          {config.tags.map(t => (
            <span key={t} style={{ fontSize: 10, padding: '2px 8px', borderRadius: 99, background: 'rgba(255,255,255,0.06)', color: 'var(--text-muted)' }}>
              {t}
            </span>
          ))}
        </div>

        {/* Compatible hardware */}
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 14 }}>
          <span style={{ color: 'var(--text)' }}>Works on: </span>
          {config.target_hardware.map(h => HW_LABELS[h] ?? h).join(' · ')}
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button
            onClick={() => downloadYaml(config)}
            style={{
              padding: '8px 16px',
              background: 'var(--cyan)',
              color: '#0e1416',
              border: 'none',
              borderRadius: 8,
              fontSize: 12,
              fontWeight: 700,
              fontFamily: 'var(--font-head)',
              cursor: 'pointer',
            }}
          >
            ↓ Download .yaml
          </button>
          <a
            href={config.yaml_url}
            target="_blank"
            rel="noopener noreferrer"
            style={{ padding: '8px 14px', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--text)' }}
          >
            View raw ↗
          </a>
          <a
            href={`opencastor://harness/config/${config.id}`}
            style={{ padding: '8px 14px', background: 'var(--amber-dim)', border: '1px solid var(--amber)', borderRadius: 8, fontSize: 12, color: 'var(--amber)' }}
          >
            Open in app →
          </a>
        </div>
      </div>
    </div>
  )
}

function CommandBlock({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
      <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--border)', fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
        {title}
      </div>
      <pre style={{ margin: 0, padding: '12px 14px', fontFamily: 'var(--font-mono)', fontSize: 11, lineHeight: 1.8, color: 'var(--text)', overflowX: 'auto' }}>
        {lines.map((line, i) => (
          <span key={i} style={{ display: 'block', color: line.startsWith('#') ? 'var(--text-muted)' : line === '' ? 'transparent' : 'var(--cyan)' }}>
            {line || ' '}
          </span>
        ))}
      </pre>
    </div>
  )
}

export function ConfigLibrary() {
  return (
    <section style={{ margin: '48px 0' }}>
      <div style={{ marginBottom: 8 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--cyan)', letterSpacing: 1 }}>CONFIG LIBRARY</span>
      </div>
      <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Shareable Harness Configs</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: 14, maxWidth: 640, marginBottom: 10, lineHeight: 1.7 }}>
        Every config is a plain <code style={{ fontFamily: 'var(--font-mono)', color: 'var(--cyan)', fontSize: 13 }}>.yaml</code> file — download it, apply it to one robot or an entire fleet. Competition winners are automatically added here. Anyone can contribute a config via a GitHub PR.
      </p>
      <div style={{ marginBottom: 28 }}>
        <a href="https://raw.githubusercontent.com/craigm26/OpenCastor/main/research/index.json" target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Machine-readable index: research/index.json ↗
        </a>
        {' · '}
        <a href="https://github.com/craigm26/OpenCastor/tree/main/research/presets" target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Browse all on GitHub ↗
        </a>
      </div>

      {/* Config cards */}
      <div style={{ display: 'grid', gap: 16, marginBottom: 36 }}>
        {CONFIGS.map(c => <ConfigCard key={c.id} config={c} />)}
      </div>

      {/* Apply instructions */}
      <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>Apply a config — three ways</h3>
      <div style={{ display: 'grid', gap: 12 }}>
        <CommandBlock title="Single robot — CLI" lines={CLI_COMMANDS.single_robot} />
        <CommandBlock title="Swarm / fleet broadcast — CLI" lines={CLI_COMMANDS.swarm} />
        <CommandBlock title="OpenCastor app — tap-to-apply" lines={CLI_COMMANDS.app} />
      </div>

      {/* Safety note */}
      <div style={{ marginTop: 20, padding: '14px 18px', border: '1px solid rgba(74,222,128,0.25)', borderRadius: 10, background: 'rgba(74,222,128,0.04)', fontSize: 13, color: 'var(--text)', lineHeight: 1.7 }}>
        <strong style={{ color: '#4ade80' }}>🔒 Safety guarantee: </strong>
        Configs downloaded from this library are validated before apply. P66 physical consent, ESTOP logic, and motor parameters cannot be modified by any harness config — they are stripped on apply. You are always asked to confirm before a config is written to your robot.
      </div>
    </section>
  )
}
