/**
 * OpenCastor JavaScript / TypeScript SDK
 *
 * Thin, zero-dependency client for the OpenCastor API gateway.
 *
 * Usage (ESM / Node / Deno / browser):
 *   import { CastorClient } from "@opencastor/sdk";
 *   const client = new CastorClient({ baseUrl: "http://localhost:8000", token: "..." });
 *   const status = await client.status();
 *   await client.command("go forward slowly");
 *
 * Install:
 *   npm install @opencastor/sdk
 */

// ── Types ────────────────────────────────────────────────────────────────────

export interface CastorClientOptions {
  /** Base URL of the OpenCastor gateway, e.g. "http://192.168.1.10:8000" */
  baseUrl: string;
  /** Bearer token (OPENCASTOR_API_TOKEN). Optional for open deployments. */
  token?: string;
  /** Request timeout in milliseconds (default: 10000) */
  timeoutMs?: number;
}

export interface StatusResponse {
  status: string;
  version: string;
  provider: string;
  driver: string;
  uptime_s: number;
  [key: string]: unknown;
}

export interface CommandResponse {
  thought?: string;
  action?: Record<string, unknown>;
  latency_ms?: number;
  [key: string]: unknown;
}

export interface Detection {
  class: string;
  confidence: number;
  bbox: { x1: number; y1: number; x2: number; y2: number };
  center: { x: number; y: number };
}

export interface DetectionResponse {
  detections: Detection[];
  latency_ms: number;
  mode: string;
}

export interface PointCloudResponse {
  point_count: number;
  points: [number, number, number][];
  bounds: { x: number[]; y: number[]; z: number[] };
  mode: string;
}

export interface EpisodeMemory {
  id?: number | string;
  timestamp?: string;
  instruction?: string;
  thought?: string;
  action?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface TelemetryFrame {
  loop_latency_ms?: number;
  battery_v?: number;
  provider?: string;
  obstacles?: { left_cm: number; center_cm: number; right_cm: number; nearest_cm: number };
  [key: string]: unknown;
}

export type TelemetryCallback = (frame: TelemetryFrame) => void;

// ── Client ───────────────────────────────────────────────────────────────────

export class CastorClient {
  private readonly baseUrl: string;
  private readonly token: string | undefined;
  private readonly timeoutMs: number;

  constructor(options: CastorClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.token = options.token;
    this.timeoutMs = options.timeoutMs ?? 10_000;
  }

  // ── Internal ──────────────────────────────────────────────────────────

  private headers(extra?: Record<string, string>): Record<string, string> {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (this.token) h["Authorization"] = `Bearer ${this.token}`;
    return { ...h, ...extra };
  }

  private url(path: string): string {
    return `${this.baseUrl}${path}`;
  }

  private async fetch<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const controller = new AbortController();
    const tid = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await fetch(this.url(path), {
        method,
        headers: this.headers(),
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new CastorError(res.status, text);
      }
      return res.json() as Promise<T>;
    } finally {
      clearTimeout(tid);
    }
  }

  // ── Core ──────────────────────────────────────────────────────────────

  /** Check gateway liveness. */
  async health(): Promise<{ status: string }> {
    return this.fetch("GET", "/health");
  }

  /** Full robot status (provider, driver, channels, uptime). */
  async status(): Promise<StatusResponse> {
    return this.fetch("GET", "/api/status");
  }

  /** Send a natural-language instruction to the robot brain. */
  async command(instruction: string, imageBase64?: string): Promise<CommandResponse> {
    return this.fetch("POST", "/api/command", { instruction, image: imageBase64 });
  }

  /** Stream a command response. Calls `onChunk` for each NDJSON line. */
  async commandStream(
    instruction: string,
    onChunk: (chunk: string) => void,
    imageBase64?: string,
  ): Promise<void> {
    const res = await fetch(this.url("/api/command/stream"), {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify({ instruction, image: imageBase64 }),
    });
    if (!res.ok || !res.body) throw new CastorError(res.status, await res.text().catch(() => ""));
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      for (const line of lines) {
        if (line.trim()) onChunk(line.trim());
      }
    }
    if (buf.trim()) onChunk(buf.trim());
  }

  /** Send a raw action dict directly to the driver. */
  async action(actionDict: Record<string, unknown>): Promise<{ ok: boolean }> {
    return this.fetch("POST", "/api/action", actionDict);
  }

  /** Emergency stop. */
  async stop(): Promise<{ ok: boolean }> {
    return this.fetch("POST", "/api/stop", {});
  }

  // ── Memory ────────────────────────────────────────────────────────────

  /** Fetch recent episode memories. */
  async episodes(limit = 20): Promise<EpisodeMemory[]> {
    return this.fetch("GET", `/api/memory/episodes?limit=${limit}`);
  }

  /** Export all memories as JSONL string. */
  async exportMemory(): Promise<string> {
    const controller = new AbortController();
    const tid = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await fetch(this.url("/api/memory/export"), {
        headers: this.token ? { Authorization: `Bearer ${this.token}` } : {},
        signal: controller.signal,
      });
      if (!res.ok) throw new CastorError(res.status, await res.text().catch(() => ""));
      return res.text();
    } finally {
      clearTimeout(tid);
    }
  }

  /** Delete all stored episodes. */
  async clearMemory(): Promise<{ ok: boolean }> {
    return this.fetch("DELETE", "/api/memory/episodes");
  }

  // ── Detection ─────────────────────────────────────────────────────────

  /** Get latest object detections. Triggers a new detection cycle if camera is available. */
  async detectLatest(): Promise<DetectionResponse> {
    return this.fetch("GET", "/api/detection/latest");
  }

  /** Get the annotated detection frame URL (for use in <img> tags). */
  detectionFrameUrl(): string {
    const auth = this.token ? `?token=${encodeURIComponent(this.token)}` : "";
    return `${this.baseUrl}/api/detection/frame${auth}`;
  }

  // ── Point Cloud ───────────────────────────────────────────────────────

  /** Get latest 3D point cloud as JSON. */
  async pointCloud(): Promise<PointCloudResponse> {
    return this.fetch("GET", "/api/depth/pointcloud");
  }

  /** Get point cloud stats. */
  async pointCloudStats(): Promise<{ point_count: number; mode: string; bounds_m?: object }> {
    return this.fetch("GET", "/api/depth/pointcloud/stats");
  }

  /** Get PLY file URL (for download). */
  pointCloudPlyUrl(): string {
    return `${this.baseUrl}/api/depth/pointcloud.ply`;
  }

  // ── Navigation ────────────────────────────────────────────────────────

  /** Navigate to a waypoint using dead-reckoning. */
  async navWaypoint(distanceM: number, headingDeg: number, speed = 0.6): Promise<{ ok: boolean }> {
    return this.fetch("POST", "/api/nav/waypoint", {
      distance_m: distanceM,
      heading_deg: headingDeg,
      speed,
    });
  }

  /** Get current navigation status. */
  async navStatus(): Promise<{ running: boolean; job_id?: string }> {
    return this.fetch("GET", "/api/nav/status");
  }

  // ── Telemetry WebSocket ────────────────────────────────────────────────

  /**
   * Open a WebSocket connection to /ws/telemetry for live 5 Hz data.
   * Returns a function to close the connection.
   */
  telemetry(callback: TelemetryCallback): () => void {
    const wsBase = this.baseUrl.replace(/^http/, "ws");
    const url = this.token
      ? `${wsBase}/ws/telemetry?token=${encodeURIComponent(this.token)}`
      : `${wsBase}/ws/telemetry`;
    const ws = new WebSocket(url);
    ws.onmessage = (ev) => {
      try {
        callback(JSON.parse(ev.data) as TelemetryFrame);
      } catch {
        // ignore parse errors
      }
    };
    return () => ws.close();
  }

  // ── Sim ───────────────────────────────────────────────────────────────

  /** List supported simulation export formats. */
  async simFormats(): Promise<{ formats: string[] }> {
    return this.fetch("GET", "/api/sim/formats");
  }

  /** Export episodes to a simulation format. Returns path + metadata. */
  async simExport(
    format: "json" | "mjcf" | "sdf" | "hdf5" | "gym",
    limit = 50,
  ): Promise<{ path: string; format: string; episode_count: number; size_bytes: number }> {
    return this.fetch("GET", `/api/sim/export/${format}?limit=${limit}`);
  }

  /** Get a simulation config file (MuJoCo XML or Gazebo SDF). */
  async simConfig(sim: "mujoco" | "gazebo"): Promise<string> {
    const res = await fetch(this.url(`/api/sim/config/${sim}`), {
      headers: this.token ? { Authorization: `Bearer ${this.token}` } : {},
    });
    if (!res.ok) throw new CastorError(res.status, await res.text().catch(() => ""));
    return res.text();
  }
}

// ── Error ─────────────────────────────────────────────────────────────────────

export class CastorError extends Error {
  constructor(
    public readonly statusCode: number,
    public readonly body: string,
  ) {
    super(`CastorError ${statusCode}: ${body}`);
    this.name = "CastorError";
  }
}

// ── Default export ────────────────────────────────────────────────────────────

export default CastorClient;
