export type Domain = 'general' | 'home' | 'industrial'

export interface LeaderboardEntry {
  rank: number
  id: string
  location: string
  hardware: string
  model: string
  score: number
  domainScores: Record<Domain, number>
  credits: number
  safetyCertified: boolean
  harnessConfig: string
  lastRun: string
  workUnits: number
}

export interface HardwareTier {
  id: string
  label: string
  icon: string
  subtitle: string
  entries: LeaderboardEntry[]
}

export interface SynthesisInsight {
  domain: Domain
  finding: string
  winningPattern: string
  dataPoints: number
  confidence: 'high' | 'medium' | 'emerging'
}

function daysAgo(n: number) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

export const TIERS: HardwareTier[] = [
  {
    id: 'pi5-hailo',
    label: 'Pi5 + Hailo-8L',
    icon: '🏆',
    subtitle: 'NPU-accelerated edge inference',
    entries: [
      { rank: 1, id: 'Fleet_Pi5_Berlin', location: 'Berlin, DE', hardware: 'Pi5 + Hailo-8L', model: 'gemini-2.5-flash', score: 0.9241, domainScores: { general: 0.94, home: 0.91, industrial: 0.93 }, credits: 2847, safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=2048 / drift_detection=true', lastRun: daysAgo(1),  workUnits: 441 },
      { rank: 2, id: 'RobotOwner_a2c9', location: 'Tokyo, JP',  hardware: 'Pi5 + Hailo-8L', model: 'gemini-2.5-flash', score: 0.9108, domainScores: { general: 0.92, home: 0.90, industrial: 0.91 }, credits: 2201, safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=1024 / context_budget=16384', lastRun: daysAgo(2),  workUnits: 389 },
      { rank: 3, id: 'HailoBot_7f3a',   location: 'Austin, TX', hardware: 'Pi5 + Hailo-8L', model: 'gemini-2.5-flash', score: 0.8979, domainScores: { general: 0.91, home: 0.88, industrial: 0.90 }, credits: 1834, safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=1024 / retry_on_error=true', lastRun: daysAgo(4),  workUnits: 312 },
      { rank: 4, id: 'EdgeFleet_Oslo',   location: 'Oslo, NO',   hardware: 'Pi5 + Hailo-8L', model: 'gemini-2.5-flash', score: 0.8712, domainScores: { general: 0.89, home: 0.85, industrial: 0.87 }, credits: 1102, safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=512  / drift_detection=true', lastRun: daysAgo(7),  workUnits: 201 },
      { rank: 5, id: 'NpuNode_Seoul',    location: 'Seoul, KR',  hardware: 'Pi5 + Hailo-8L', model: 'gemma3:4b',        score: 0.8441, domainScores: { general: 0.87, home: 0.82, industrial: 0.84 }, credits: 891,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=1024 / context_budget=8192',  lastRun: daysAgo(9),  workUnits: 178 },
    ],
  },
  {
    id: 'pi5-8gb',
    label: 'Pi5 8GB',
    icon: '🥈',
    subtitle: 'Raspberry Pi 5 · 8 GB RAM',
    entries: [
      { rank: 1, id: 'RobotOwner_7f3a',   location: 'Portland, OR', hardware: 'Pi5 8GB', model: 'gemini-2.5-flash', score: 0.8812, domainScores: { general: 0.90, home: 0.87, industrial: 0.88 }, credits: 1203, safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=1024 / context_budget=8192',  lastRun: daysAgo(2),  workUnits: 288 },
      { rank: 2, id: 'PiFleet_Munich',     location: 'Munich, DE',   hardware: 'Pi5 8GB', model: 'gemini-2.5-flash', score: 0.8644, domainScores: { general: 0.88, home: 0.85, industrial: 0.86 }, credits: 987,  safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=1024 / drift_detection=true',  lastRun: daysAgo(3),  workUnits: 241 },
      { rank: 3, id: 'RaspBot_Chicago',    location: 'Chicago, IL',   hardware: 'Pi5 8GB', model: 'gemini-2.5-flash', score: 0.8503, domainScores: { general: 0.86, home: 0.84, industrial: 0.85 }, credits: 812,  safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=512  / retry_on_error=true',   lastRun: daysAgo(5),  workUnits: 197 },
      { rank: 4, id: 'NodeBot_London',     location: 'London, UK',    hardware: 'Pi5 8GB', model: 'gemma3:4b',        score: 0.8211, domainScores: { general: 0.84, home: 0.80, industrial: 0.82 }, credits: 634,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=1024 / context_budget=4096',  lastRun: daysAgo(8),  workUnits: 153 },
      { rank: 5, id: 'HomeBot_Sydney',     location: 'Sydney, AU',    hardware: 'Pi5 8GB', model: 'gemma3:4b',        score: 0.7988, domainScores: { general: 0.82, home: 0.78, industrial: 0.79 }, credits: 501,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=512  / drift_detection=false', lastRun: daysAgo(11), workUnits: 121 },
    ],
  },
  {
    id: 'pi5-4gb',
    label: 'Pi5 4GB',
    icon: '🥉',
    subtitle: 'Raspberry Pi 5 · 4 GB RAM',
    entries: [
      { rank: 1, id: 'Raspi_Portland',  location: 'Portland, OR', hardware: 'Pi5 4GB', model: 'gemma3:1b', score: 0.8103, domainScores: { general: 0.83, home: 0.80, industrial: 0.80 }, credits: 891,  safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=512 / context_budget=4096 / local_model=gemma3:1b', lastRun: daysAgo(1),  workUnits: 203 },
      { rank: 2, id: 'LiteBot_Denver',  location: 'Denver, CO',   hardware: 'Pi5 4GB', model: 'gemma3:1b', score: 0.7941, domainScores: { general: 0.81, home: 0.78, industrial: 0.79 }, credits: 712,  safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=512 / retry_on_error=true',   lastRun: daysAgo(3),  workUnits: 178 },
      { rank: 3, id: 'EdgePi_Helsinki', location: 'Helsinki, FI', hardware: 'Pi5 4GB', model: 'gemma3:1b', score: 0.7788, domainScores: { general: 0.80, home: 0.76, industrial: 0.77 }, credits: 588,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=256 / context_budget=4096',   lastRun: daysAgo(6),  workUnits: 142 },
      { rank: 4, id: 'TinyBot_NYC',     location: 'New York, NY', hardware: 'Pi5 4GB', model: 'smollm2',   score: 0.7412, domainScores: { general: 0.76, home: 0.73, industrial: 0.74 }, credits: 401,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=256 / context_budget=2048',   lastRun: daysAgo(9),  workUnits: 98  },
      { rank: 5, id: 'MiniFleet_Paris', location: 'Paris, FR',    hardware: 'Pi5 4GB', model: 'smollm2',   score: 0.7189, domainScores: { general: 0.74, home: 0.71, industrial: 0.70 }, credits: 312,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=128 / drift_detection=false',  lastRun: daysAgo(14), workUnits: 77  },
    ],
  },
  {
    id: 'jetson',
    label: 'Jetson Nano',
    icon: '⚡',
    subtitle: 'NVIDIA Jetson Nano · GPU edge',
    entries: [
      { rank: 1, id: 'EdgeBot_Tokyo',   location: 'Tokyo, JP',      hardware: 'Jetson Nano', model: 'llama3.2:3b', score: 0.7944, domainScores: { general: 0.82, home: 0.77, industrial: 0.79 }, credits: 654,  safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=1024 / local_model=llama3.2:3b', lastRun: daysAgo(2),  workUnits: 156 },
      { rank: 2, id: 'GpuBot_Seattle',  location: 'Seattle, WA',    hardware: 'Jetson Nano', model: 'llama3.2:3b', score: 0.7801, domainScores: { general: 0.80, home: 0.76, industrial: 0.78 }, credits: 521,  safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=512  / retry_on_error=true',   lastRun: daysAgo(4),  workUnits: 134 },
      { rank: 3, id: 'NvBot_Taipei',    location: 'Taipei, TW',     hardware: 'Jetson Nano', model: 'llama3.2:3b', score: 0.7612, domainScores: { general: 0.78, home: 0.74, industrial: 0.76 }, credits: 412,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=512  / context_budget=8192',  lastRun: daysAgo(7),  workUnits: 108 },
      { rank: 4, id: 'JetFleet_Rome',   location: 'Rome, IT',       hardware: 'Jetson Nano', model: 'gemma3:1b',   score: 0.7341, domainScores: { general: 0.75, home: 0.72, industrial: 0.73 }, credits: 301,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=256  / context_budget=4096',  lastRun: daysAgo(10), workUnits: 79  },
      { rank: 5, id: 'EdgeGpu_Toronto', location: 'Toronto, CA',    hardware: 'Jetson Nano', model: 'gemma3:1b',   score: 0.7102, domainScores: { general: 0.73, home: 0.69, industrial: 0.71 }, credits: 218,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=256  / drift_detection=false', lastRun: daysAgo(13), workUnits: 61  },
    ],
  },
  {
    id: 'server',
    label: 'Server / Cloud',
    icon: '☁️',
    subtitle: 'High-compute · no resource limit',
    entries: [
      { rank: 1, id: 'CloudFleet_NYC',   location: 'New York, NY',    hardware: 'Cloud / GPU Server', model: 'claude-sonnet-4-6', score: 0.9801, domainScores: { general: 0.98, home: 0.97, industrial: 0.99 }, credits: 4102, safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=4096 / context_budget=32768 / drift_detection=true', lastRun: daysAgo(1),  workUnits: 612 },
      { rank: 2, id: 'ServerBot_SF',     location: 'San Francisco, CA', hardware: 'Cloud / GPU Server', model: 'claude-sonnet-4-6', score: 0.9688, domainScores: { general: 0.97, home: 0.96, industrial: 0.97 }, credits: 3801, safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=4096 / context_budget=16384 / retry_on_error=true',  lastRun: daysAgo(1),  workUnits: 578 },
      { rank: 3, id: 'HighComp_London',  location: 'London, UK',       hardware: 'Cloud / GPU Server', model: 'gemini-2.5-pro',    score: 0.9512, domainScores: { general: 0.96, home: 0.94, industrial: 0.95 }, credits: 3201, safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=2048 / context_budget=32768 / drift_detection=true', lastRun: daysAgo(2),  workUnits: 489 },
      { rank: 4, id: 'FleetServer_Syd',  location: 'Sydney, AU',       hardware: 'Cloud / GPU Server', model: 'gemini-2.5-pro',    score: 0.9341, domainScores: { general: 0.94, home: 0.93, industrial: 0.93 }, credits: 2788, safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=2048 / context_budget=16384',                         lastRun: daysAgo(3),  workUnits: 401 },
      { rank: 5, id: 'CloudNode_Berlin', location: 'Berlin, DE',       hardware: 'Cloud / GPU Server', model: 'claude-sonnet-4-6', score: 0.9201, domainScores: { general: 0.93, home: 0.91, industrial: 0.92 }, credits: 2401, safetyCertified: true,  harnessConfig: 'lower_cost / thinking_budget=1024 / context_budget=32768',                         lastRun: daysAgo(4),  workUnits: 367 },
    ],
  },
  {
    id: 'waveshare',
    label: 'WaveShare / Budget',
    icon: '🔧',
    subtitle: 'WaveShare 10-DOF + Pi Zero / 3B',
    entries: [
      { rank: 1, id: 'WS_Contributor_1', location: 'Minneapolis, MN', hardware: 'WaveShare 10-DOF + Pi3B', model: 'gemini-2.5-flash', score: 0.7312, domainScores: { general: 0.75, home: 0.72, industrial: 0.72 }, credits: 412,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=512 / context_budget=4096',   lastRun: daysAgo(3),  workUnits: 98  },
      { rank: 2, id: 'BudgetBot_Indy',   location: 'Indianapolis, IN', hardware: 'WaveShare 10-DOF + Pi3B', model: 'gemini-2.5-flash', score: 0.7144, domainScores: { general: 0.73, home: 0.71, industrial: 0.70 }, credits: 334,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=256 / retry_on_error=true',    lastRun: daysAgo(5),  workUnits: 81  },
      { rank: 3, id: 'ZeroBot_Dallas',   location: 'Dallas, TX',       hardware: 'WaveShare 10-DOF + Pi Zero2', model: 'smollm2',      score: 0.6891, domainScores: { general: 0.71, home: 0.68, industrial: 0.68 }, credits: 241,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=128 / context_budget=2048',   lastRun: daysAgo(8),  workUnits: 63  },
      { rank: 4, id: 'WS_Node_Phoenix',  location: 'Phoenix, AZ',      hardware: 'WaveShare 10-DOF + Pi Zero2', model: 'smollm2',      score: 0.6712, domainScores: { general: 0.69, home: 0.66, industrial: 0.66 }, credits: 189,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=128 / drift_detection=false',  lastRun: daysAgo(11), workUnits: 49  },
      { rank: 5, id: 'MicroBot_Atlanta', location: 'Atlanta, GA',      hardware: 'WaveShare 10-DOF + Pi Zero2', model: 'smollm2',      score: 0.6541, domainScores: { general: 0.67, home: 0.65, industrial: 0.64 }, credits: 142,  safetyCertified: false, harnessConfig: 'lower_cost / thinking_budget=64  / context_budget=2048',   lastRun: daysAgo(15), workUnits: 37  },
    ],
  },
]

export const SYNTHESIS_INSIGHTS: SynthesisInsight[] = [
  {
    domain: 'general',
    finding: 'Cloud models dominate general reasoning but NPU-accelerated edge hardware closes the gap faster than expected — Pi5+Hailo8L reaches 94% of cloud scores at ~3% of the compute cost.',
    winningPattern: 'thinking_budget ≥ 1024 + drift_detection=true across all tiers',
    dataPoints: 2341,
    confidence: 'high',
  },
  {
    domain: 'home',
    finding: 'Home automation tasks reward low-latency local inference over raw model quality. Gemma3:1b on Pi5 4GB outperforms cloud models on "home" subtasks because P66 consent + grip sequences are latency-sensitive — a 3s API round-trip fails where a 0.3s local call succeeds.',
    winningPattern: 'local_model=gemma3:1b + context_budget=4096 for home tier',
    dataPoints: 1887,
    confidence: 'high',
  },
  {
    domain: 'industrial',
    finding: 'Industrial tasks require alert() and sensor_read() tool calls that cloud models handle better due to richer grounding data. retry_on_error=true is the single config flag most correlated with industrial score improvement (+12% median).',
    winningPattern: 'retry_on_error=true + thinking_budget ≥ 1024 for industrial',
    dataPoints: 1204,
    confidence: 'medium',
  },
]

export const TOTAL_RUNS = 7341
export const TOTAL_ROBOTS = 127
export const SEARCH_SPACE_EXPLORED = 1.7  // percent
export const CHAMPION_SCORE = 0.9801
export const CHAMPION_CONFIG = 'lower_cost / thinking_budget=4096 / context_budget=32768 / drift_detection=true'
