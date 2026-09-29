import { afterEach, expect, it, vi } from "vitest";
import { runFileCheck } from "./materialCheckJobs";
import { setSessionToken } from "../auth/sessionTransport";

const id = "50000000-0000-4000-8000-000000000001", jobId = "60000000-0000-4000-8000-000000000001";
const selection = [{ id, updatedAt: "2026-09-29T10:00:00Z" }];
const running = { id: jobId, status: "RUNNING", total: 1, completed: 0, active: [{ material_id: id, identity: "BRAND_0001_NAME_A01", file: "4K/BRAND_0001_NAME_COL_4K.jpg", phase: "checking" }], elapsed_seconds: 5, cache_hits: 2, cache_misses: 1, result: null, error: null };
const report = { items: [{ material_id: id, status: "OK", checked_at: selection[0].updatedAt, updated_at: selection[0].updatedAt, profile: "PBR_FILES_V1", complete: true, issues: [], report: "OK" }], report: "Checked 1; OK 1", report_path: null, report_opened: false };
const completed = { ...running, status: "COMPLETED", completed: 1, active: [], result: report };
const response = (value: unknown) => new Response(JSON.stringify(value));
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); setSessionToken(null); });

it("starts once and observes only the same job until its committed result", async () => {
  vi.useFakeTimers(); const progress = vi.fn();
  const fetch = vi.fn().mockResolvedValueOnce(response(running)).mockResolvedValueOnce(response({ ...running, completed: 1, active: [] })).mockResolvedValueOnce(response(completed));
  vi.stubGlobal("fetch", fetch);
  const promise = runFileCheck(selection, false, { onProgress: progress });
  await vi.advanceTimersByTimeAsync(0);
  expect(progress).toHaveBeenLastCalledWith(expect.objectContaining({ completed: 0, cacheHits: 2, active: [expect.objectContaining({ materialId: id, file: running.active[0].file })] }));
  await vi.advanceTimersByTimeAsync(1000);
  expect(progress).toHaveBeenLastCalledWith(expect.objectContaining({ completed: 1, status: "RUNNING" }));
  await vi.advanceTimersByTimeAsync(1000);
  expect(await promise).toMatchObject({ items: [{ materialId: id, status: "OK" }], report: report.report });
  expect(fetch.mock.calls.map(call => [call[0], call[1].method])).toEqual([
    ["/api/materials/check-jobs", "POST"], [`/api/materials/check-jobs/${jobId}`, "GET"], [`/api/materials/check-jobs/${jobId}`, "GET"],
  ]);
});

it("pauses a failed poll and resumes the same job without another POST", async () => {
  vi.useFakeTimers(); const paused = vi.fn();
  const fetch = vi.fn().mockResolvedValueOnce(response(running)).mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce(response(completed));
  vi.stubGlobal("fetch", fetch);
  const promise = runFileCheck(selection, true, { onPaused: paused });
  await vi.advanceTimersByTimeAsync(1000);
  expect(paused).toHaveBeenCalledOnce(); expect(fetch).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(5000); expect(fetch).toHaveBeenCalledTimes(2);
  paused.mock.calls[0][0]();
  await expect(promise).resolves.toMatchObject({ report: report.report });
  expect(fetch.mock.calls.filter(call => call[1].method === "POST")).toHaveLength(1);
});

it("replays an uncertain start with the same idempotency key and frozen selection", async () => {
  vi.useFakeTimers(); const paused = vi.fn();
  const fetch = vi.fn().mockRejectedValueOnce(new TypeError("lost POST response")).mockResolvedValueOnce(response(completed));
  vi.stubGlobal("fetch", fetch);
  const promise = runFileCheck(selection, true, { onPaused: paused });
  await vi.advanceTimersByTimeAsync(0); paused.mock.calls[0][0]();
  await promise;
  expect(fetch.mock.calls[1]).toEqual(fetch.mock.calls[0]);
  expect(fetch.mock.calls[0][1].headers["Idempotency-Key"]).toMatch(/^[a-f0-9-]{36}$/);
});

it("stops on an expired job instead of starting a replacement", async () => {
  vi.useFakeTimers(); const paused = vi.fn();
  const fetch = vi.fn().mockResolvedValueOnce(response(running)).mockResolvedValueOnce(new Response("{}", { status: 404 }));
  vi.stubGlobal("fetch", fetch);
  const outcome = runFileCheck(selection, true, { onPaused: paused }).catch(error => error);
  await vi.advanceTimersByTimeAsync(1000);
  expect(await outcome).toMatchObject({ status: 404 }); expect(paused).not.toHaveBeenCalled(); expect(fetch).toHaveBeenCalledTimes(2);
});

it("does not deliver progress or a private result to a changed session", async () => {
  const progress = vi.fn(); let finish!: (value: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise(resolve => { finish = resolve; })));
  const outcome = runFileCheck(selection, true, { onProgress: progress }).catch(error => error);
  setSessionToken(null); finish(response(completed));
  expect(await outcome).toMatchObject({ name: "AbortError" }); expect(progress).not.toHaveBeenCalled();
});

it("aborts a paused observer when its component unmounts", async () => {
  vi.useFakeTimers(); const paused = vi.fn(), controller = new AbortController();
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
  const outcome = runFileCheck(selection, true, { onPaused: paused, signal: controller.signal }).catch(error => error);
  await vi.advanceTimersByTimeAsync(0); expect(paused).toHaveBeenCalledOnce(); controller.abort();
  expect(await outcome).toMatchObject({ name: "AbortError" });
});

it.each([
  { total: 2 }, { completed: 2 }, { active: [{ ...running.active[0], material_id: jobId }] },
  { status: "COMPLETED", result: report }, { elapsed_seconds: -1 }, { cache_hits: 1.5 },
])("rejects inconsistent job progress", async change => {
  vi.stubGlobal("fetch", vi.fn(async () => response({ ...running, ...change })));
  await expect(runFileCheck(selection, true)).rejects.toThrow();
});

it("reports a terminal server failure without exposing any successful result", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => response({ ...running, status: "FAILED", error: { code: "LOCAL_SOURCE_CHANGED", message: "Source changed during inspection." } })));
  await expect(runFileCheck(selection, true)).rejects.toMatchObject({ code: "LOCAL_SOURCE_CHANGED" });
});
