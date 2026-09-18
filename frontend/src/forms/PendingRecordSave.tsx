import type { RecordCommandController } from "./useRecordCommand";

export function PendingRecordSave({ controller }: { controller: RecordCommandController }) {
  const { ownPacket, busy } = controller;
  if (!ownPacket) return null;
  return <section className="pending-record-save" aria-label="Pending save">
    <p>Your submitted values are kept for this request. Leaving or reloading the application can lose this recovery information.</p>
    <p className="revision-hash">Request: {ownPacket.key}</p>
    {ownPacket.phase === "UNKNOWN" && <div className="form-actions">
      <button type="button" className="button" disabled={busy} onClick={() => void controller.recover()}>Check saved result</button>
      <button type="button" className="button" disabled={busy} onClick={() => void controller.retry()}>Retry exact save</button>
    </div>}
    {ownPacket.phase === "SAVED" && ownPacket.result && <button type="button" className="button" onClick={controller.acceptSaved}>Open saved record</button>}
    {ownPacket.phase === "SENDING" && <p role="status">Checking this save…</p>}
  </section>;
}
