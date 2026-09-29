import type { FileCheckProgress as Progress } from "../api/materialCheckJobs";

function duration(seconds: number) {
  const value = Math.floor(seconds);
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}

const phases: Record<string, string> = { queued: "Waiting", staging: "Preparing files", hashing: "Reading file contents", decoding: "Checking image", checking: "Checking", verifying: "Verifying source files", cached: "Reusing checked file", reused: "Reusing checked files", saving: "Saving results" };

export function FileCheckProgress({ progress, resume, total }: { progress: Progress | null; resume: (() => void) | null; total: number }) {
  // All files can be inspected before the transaction is committed. Keep the
  // bar below 100% until the server confirms the completed job and its result.
  const percentage = progress?.status === "COMPLETED" ? 100 : Math.min(99, Math.floor((progress?.completed ?? 0) / Math.max(total, 1) * 100));
  return <section className="material-check-progress" aria-label="File check progress">
    <p role="status">{progress ? `${progress.completed} / ${progress.total} materials inspected · Elapsed ${duration(progress.elapsedSeconds)}` : `Starting check of ${total} materials…`}</p>
    <progress aria-label="Automatic file check progress" max={100} value={percentage} />
    {progress && <p className="muted">Reused file checks: {progress.cacheHits} · Fresh file checks: {progress.cacheMisses}</p>}
    {!!progress?.active.length && <ul>{progress.active.map(item => <li key={item.materialId}>
      <strong>{item.identity}</strong> · {phases[item.phase.toLowerCase()] ?? "Checking"}{item.file && <> · <span className="folder-absolute-path">{item.file}</span></>}</li>)}</ul>}
    {progress?.status === "RUNNING" && progress.completed === progress.total && <p>File inspection finished. Verifying and saving results…</p>}
    {resume ? <div><p role="alert">Progress could not be refreshed. Resume the same check to recover its result.</p>
      <button type="button" className="button" onClick={resume}>Resume file check</button></div>
      : <p className="muted">Large materials may take several minutes.</p>}
  </section>;
}
