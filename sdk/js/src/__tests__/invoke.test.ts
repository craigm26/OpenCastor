/**
 * @opencastor/sdk — §19 INVOKE / INVOKE_CANCEL and §21 Registry tests
 * Uses Jest with manual fetch mocks (no real HTTP calls).
 */

import { describe, it, expect, jest, beforeEach } from "@jest/globals";
import { CastorClient, InvokeResponse, InvokeRequest } from "../index";

// ── fetch mock ───────────────────────────────────────────────────────────────
type FetchImpl = typeof globalThis.fetch;
const mockFetch = jest.fn<FetchImpl>();
globalThis.fetch = mockFetch;

function jsonResp(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockFetch.mockReset();
});

const client = new CastorClient({ baseUrl: "http://localhost:8000" });

// ── §19 invoke() ────────────────────────────────────────────────────────────

describe("invoke()", () => {
  const successResp: InvokeResponse = {
    reply_to: "test-msg-id",
    status: "success",
    result: { reached: true },
    latency_ms: 42,
  };

  it("sends INVOKE (type 11) with skill and params", async () => {
    mockFetch.mockResolvedValueOnce(jsonResp(successResp));
    const result = await client.invoke({ skill: "navigate_to", params: { x: 1, y: 2 } });
    expect(result.status).toBe("success");
    const body = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
    expect(body.msg_type).toBe(11);
    expect(body.skill).toBe("navigate_to");
    expect(body.params).toEqual({ x: 1, y: 2 });
    expect(typeof body.msg_id).toBe("string");
  });

  it("includes replyTo in the payload when provided", async () => {
    mockFetch.mockResolvedValueOnce(jsonResp(successResp));
    await client.invoke({ skill: "wave", replyTo: "rcan://other.local/bob" });
    const body = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
    expect(body.reply_to).toBe("rcan://other.local/bob");
  });

  it("omits reply_to when replyTo not provided", async () => {
    mockFetch.mockResolvedValueOnce(jsonResp(successResp));
    await client.invoke({ skill: "wave" });
    const body = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
    expect(body.reply_to).toBeUndefined();
  });

  it("uses provided msgId instead of generating one", async () => {
    mockFetch.mockResolvedValueOnce(jsonResp(successResp));
    await client.invoke({ skill: "wave", msgId: "fixed-id-123" });
    const body = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
    expect(body.msg_id).toBe("fixed-id-123");
  });

  it("returns latency_ms from response", async () => {
    mockFetch.mockResolvedValueOnce(jsonResp(successResp));
    const result = await client.invoke({ skill: "wave" });
    expect(result.latency_ms).toBe(42);
  });
});

// ── §19 invokeCancel() ──────────────────────────────────────────────────────

describe("invokeCancel()", () => {
  it("sends INVOKE_CANCEL (type 15) with invoke_id", async () => {
    mockFetch.mockResolvedValueOnce(jsonResp({ ok: true }));
    const result = await client.invokeCancel("abc-invoke-id");
    expect(result.ok).toBe(true);
    const body = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
    expect(body.msg_type).toBe(15);
    expect(body.invoke_id).toBe("abc-invoke-id");
  });
});

// ── §19 invokeAll() ─────────────────────────────────────────────────────────

describe("invokeAll()", () => {
  it("sends all skills in parallel and returns results in order", async () => {
    const r1: InvokeResponse = { reply_to: "id-1", status: "success", result: { a: 1 } };
    const r2: InvokeResponse = { reply_to: "id-2", status: "success", result: { b: 2 } };
    mockFetch
      .mockResolvedValueOnce(jsonResp(r1))
      .mockResolvedValueOnce(jsonResp(r2));
    const [res1, res2] = await client.invokeAll([
      { skill: "skill_a" },
      { skill: "skill_b" },
    ]);
    expect(res1.result).toEqual({ a: 1 });
    expect(res2.result).toEqual({ b: 2 });
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("resolves with partial results even if one skill fails", async () => {
    const r1: InvokeResponse = { reply_to: "id-1", status: "success" };
    const r2: InvokeResponse = { reply_to: "id-2", status: "error", error: "boom" };
    mockFetch
      .mockResolvedValueOnce(jsonResp(r1))
      .mockResolvedValueOnce(jsonResp(r2));
    const results = await client.invokeAll([{ skill: "ok" }, { skill: "fail" }]);
    expect(results[0].status).toBe("success");
    expect(results[1].status).toBe("error");
  });
});

// ── §21 registryRegister() ──────────────────────────────────────────────────

describe("registryRegister()", () => {
  it("sends REGISTRY_REGISTER (type 13) and returns success", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResp({ status: "success", rrn: "RRN-000000000001" })
    );
    const result = await client.registryRegister({
      rrn: "rrn://craigm26/robot/opencastor-rpi5-hailo/bob-001",
      ruri: "rcan://robot.local:8000/bob",
      name: "Bob",
      manufacturer: "craigm26",
      model: "opencastor-rpi5-hailo",
    });
    expect(result.status).toBe("success");
    expect(result.rrn).toBe("RRN-000000000001");
    const body = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
    expect(body.msg_type).toBe(13);
    expect(body.rrn).toBe("rrn://craigm26/robot/opencastor-rpi5-hailo/bob-001");
  });

  it("returns error status when registration fails", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResp({ status: "error", error: "duplicate RRN" })
    );
    const result = await client.registryRegister({
      rrn: "RRN-000000000001",
      ruri: "rcan://robot.local:8000/bob",
    });
    expect(result.status).toBe("error");
    expect(result.error).toBe("duplicate RRN");
  });
});

// ── §21 registryResolve() ───────────────────────────────────────────────────

describe("registryResolve()", () => {
  it("sends REGISTRY_RESOLVE (type 14) and returns found", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResp({
        status: "found",
        rrn: "RRN-000000000001",
        ruri: "rcan://robot.local:8000/bob",
        metadata: { model: "opencastor-rpi5-hailo" },
      })
    );
    const result = await client.registryResolve("RRN-000000000001");
    expect(result.status).toBe("found");
    expect(result.ruri).toBe("rcan://robot.local:8000/bob");
    const body = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
    expect(body.msg_type).toBe(14);
    expect(body.rrn).toBe("RRN-000000000001");
  });

  it("returns not_found for unknown RRN", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResp({ status: "not_found", rrn: "RRN-000000000099" })
    );
    const result = await client.registryResolve("RRN-000000000099");
    expect(result.status).toBe("not_found");
    expect(result.ruri).toBeUndefined();
  });

  it("resolves URI-format RRN correctly", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResp({
        status: "found",
        rrn: "rrn://craigm26/robot/opencastor-rpi5-hailo/bob-001",
        ruri: "rcan://robot.local:8000/bob",
      })
    );
    const result = await client.registryResolve(
      "rrn://craigm26/robot/opencastor-rpi5-hailo/bob-001"
    );
    expect(result.status).toBe("found");
    expect(result.ruri).toBe("rcan://robot.local:8000/bob");
  });
});
