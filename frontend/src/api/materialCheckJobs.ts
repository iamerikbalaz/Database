import { request } from "./client";
import { ApiError } from "./errors";
import { boolean, nullable, record, string, uuid } from "./dto";
import { sessionGeneration } from "../auth/sessionTransport";

export function checkResult(input: unknown) {
  const value = record(input);
  if (!Array.isArray(value.issues) || value.issues.length > 4096) throw new Error("Invalid check issues");
  const status = value.status;
  if (status !== "NOT_CHECKED" && status !== "OK" && status !== "ISSUES") throw new Error("Invalid check status");
  return { materialId: uuid(value.material_id), status, checkedAt: string(value.checked_at),
    updatedAt: string(value.updated_at), profile: string(value.profile), complete: boolean(value.complete),
    report: string(value.report), issues: value.issues.map(string) };
}

export type AutomaticFileCheckResult = ReturnType<typeof checkResult>;
export type FileCheckSelection = { id: string; updatedAt?: string };
export type FileCheckProgress = {
  id: string; status: "RUNNING" | "COMPLETED" | "FAILED"; total: number; completed: number;
  active: { materialId: string; identity: string; file: string | null; phase: string }[];
  elapsedSeconds: number; cacheHits: number; cacheMisses: number;
};
export type FileCheckOptions = {
  onProgress?: (progress: FileCheckProgress) => void;
  onPaused?: (resume: () => void) => void;
  signal?: AbortSignal;
  expectedUpdatedAt?: string;
};

export class FileCheckJobUnavailableError extends ApiError {
  constructor() { super(404, "This check is no longer available. Refresh materials to see any saved result before starting another check."); }
}

function bulkResult(input: unknown, ids: string[]) {
  const value = record(input);
  if (!Array.isArray(value.items) || value.items.length !== ids.length) throw new Error("Invalid bulk check result");
  const items = value.items.map(checkResult);
  if (new Set(items.map(item => item.materialId)).size !== ids.length || items.some(item => !ids.includes(item.materialId))) throw new Error("Invalid bulk check selection");
  return { items, report: string(value.report), reportPath: nullable(value.report_path), reportOpened: boolean(value.report_opened) };
}

function count(value: unknown) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error("Invalid check count");
  return value;
}

function parseJob(input: unknown, ids: string[], expectedId?: string) {
  const value = record(input), id = uuid(value.id), status = value.status;
  if (expectedId && id !== expectedId || status !== "RUNNING" && status !== "COMPLETED" && status !== "FAILED") throw new Error("Invalid check job");
  const total = count(value.total), completed = count(value.completed);
  if (total !== ids.length || completed > total || status === "COMPLETED" && completed !== total) throw new Error("Invalid check progress");
  if (!Array.isArray(value.active) || value.active.length > total) throw new Error("Invalid active checks");
  const active = value.active.map(input => {
    const item = record(input), materialId = uuid(item.material_id), identity = string(item.identity), file = nullable(item.file), phase = string(item.phase);
    if (!ids.includes(materialId) || identity.length > 512 || (file?.length ?? 0) > 2048 || phase.length > 100) throw new Error("Invalid active material");
    return { materialId, identity, file, phase };
  });
  if (new Set(active.map(item => item.materialId)).size !== active.length) throw new Error("Duplicate active material");
  if (typeof value.elapsed_seconds !== "number" || !Number.isFinite(value.elapsed_seconds) || value.elapsed_seconds < 0) throw new Error("Invalid check duration");
  const progress: FileCheckProgress = { id, status, total, completed, active, elapsedSeconds: value.elapsed_seconds,
    cacheHits: count(value.cache_hits), cacheMisses: count(value.cache_misses) };
  const result = value.result === null ? null : bulkResult(value.result, ids);
  const error = value.error === null ? null : record(value.error);
  if (status === "COMPLETED" ? !result || error : result !== null) throw new Error("Invalid check outcome");
  if (status === "FAILED") {
    if (!error || !/^[A-Z][A-Z0-9_]{0,99}$/.test(string(error.code)) || string(error.message).length > 2000) throw new Error("Invalid check failure");
  } else if (error !== null) throw new Error("Unexpected check failure");
  return { progress, result, error };
}

function aborted() { return new DOMException("Check observation canceled", "AbortError"); }

function delay(signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const finish = () => { signal?.removeEventListener("abort", cancel); resolve(); };
    const timer = window.setTimeout(finish, 1000);
    const cancel = () => { window.clearTimeout(timer); signal?.removeEventListener("abort", cancel); reject(aborted()); };
    if (signal?.aborted) cancel(); else signal?.addEventListener("abort", cancel, { once: true });
  });
}

export async function runFileCheck(materials: FileCheckSelection[], openReport: boolean, options: FileCheckOptions = {}) {
  if (materials.length < 1 || materials.length > 100 || new Set(materials.map(item => item.id)).size !== materials.length) throw new Error("Select between 1 and 100 distinct materials");
  const ids = materials.map(item => uuid(item.id)), generation = sessionGeneration(), requestKey = crypto.randomUUID();
  const payload = { materials: materials.map(item => ({ id: uuid(item.id), ...(item.updatedAt ? { expected_updated_at: item.updatedAt } : {}) })), open_report: openReport };
  const guard = () => { if (options.signal?.aborted || generation !== sessionGeneration()) throw aborted(); };
  const pause = (cause: unknown) => {
    guard();
    if (!options.onPaused || cause instanceof ApiError && cause.status >= 400 && cause.status < 500) throw cause;
    return new Promise<void>((resolve, reject) => {
      let resumed = false;
      const cancel = () => { options.signal?.removeEventListener("abort", cancel); reject(aborted()); };
      options.signal?.addEventListener("abort", cancel, { once: true });
      options.onPaused!(() => {
        if (resumed) return;
        resumed = true; options.signal?.removeEventListener("abort", cancel); resolve();
      });
    });
  };
  let jobId: string | undefined;
  while (true) {
    guard();
    let snapshot: ReturnType<typeof parseJob>;
    try {
      const raw = jobId
        ? await request(`/materials/check-jobs/${jobId}`, "GET", undefined, undefined, options.signal)
        : await request("/materials/check-jobs", "POST", payload, requestKey, options.signal);
      guard(); snapshot = parseJob(raw, ids, jobId);
    } catch (cause) {
      if (jobId && cause instanceof ApiError && cause.status === 404) throw new FileCheckJobUnavailableError();
      await pause(cause); continue;
    }
    jobId = snapshot.progress.id;
    options.onProgress?.(snapshot.progress);
    if (snapshot.progress.status === "COMPLETED") return snapshot.result!;
    if (snapshot.progress.status === "FAILED") {
      const failure = new ApiError(409, string(snapshot.error!.message));
      failure.code = string(snapshot.error!.code); throw failure;
    }
    await delay(options.signal);
  }
}
