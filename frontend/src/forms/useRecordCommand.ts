import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ApiError, type ValidationIssue } from "../api/errors";
import { resourceCommandClient, resourceRequestHash, type CommandScope } from "../api/resourceCommandClient";
import { pendingRecordCommands, type PendingRecordCommand, type SavedRecord } from "./pendingRecordCommands";
import type { Values } from "./fields";

export type RecordCommandDefinition = CommandScope & { payload: (values: Values) => object };
function sameScope(first: CommandScope, second?: CommandScope) {
  return second !== undefined && first.kind === second.kind && first.action === second.action &&
    first.targetId === second.targetId && first.editorPath === second.editorPath;
}
export function useRecordCommand({ actorId, command, save, onStart, onFailure, onSaved }: {
  actorId?: string; command?: RecordCommandDefinition; save: PendingRecordCommand["save"];
  onStart: () => void; onFailure: (failure: { message: string; issues: ValidationIssue[] }) => void;
  onSaved: (result: SavedRecord) => void;
}) {
  const [anonymous] = useState(() => Symbol("unmounted form")), owner = actorId ?? anonymous;
  const packet = useSyncExternalStore(pendingRecordCommands.subscribe, () => pendingRecordCommands.get(owner));
  const ownPacket = packet && sameScope(packet.scope, command) ? packet : null;
  const scopeIdentity = JSON.stringify([command?.kind, command?.action, command?.targetId, command?.editorPath]);
  // A promise owns the component/account/target lifetime in which it started.
  // Returning to the same account later does not reactivate an old callback.
  const lifetime = useMemo(() => Symbol(`${String(owner)}:${scopeIdentity}`), [owner, scopeIdentity]);
  const active = useRef<symbol | null>(null), locked = useRef<symbol | null>(null);
  const [sending, setSending] = useState<typeof lifetime | null>(null);
  const busy = sending === lifetime || ownPacket?.phase === "SENDING";
  useEffect(() => {
    active.current = lifetime;
    return () => { if (active.current === lifetime) active.current = null; if (!actorId) pendingRecordCommands.set(owner, null); };
  }, [actorId, owner, lifetime]);

  function finish(current: PendingRecordCommand, result: SavedRecord) {
    if (pendingRecordCommands.get(owner)?.key !== current.key) return;
    pendingRecordCommands.set(owner, null);
    if (active.current === lifetime) onSaved(result);
  }
  async function sendPacket(current: PendingRecordCommand, recover = false) {
    const stored = pendingRecordCommands.get(owner);
    if (!sameScope(current.scope, command) || locked.current === lifetime || stored?.phase === "SENDING" || (stored && stored.key !== current.key)) return;
    locked.current = lifetime; setSending(lifetime); onStart();
    pendingRecordCommands.set(owner, { ...current, phase: "SENDING" });
    try {
      if (recover && !actorId) throw new Error("An authenticated account is required");
      const result = recover ? await resourceCommandClient.recover(current.scope, actorId!, current.key, current.requestHash) : await current.save(current.values, current.key);
      if (active.current === lifetime) finish(current, result);
      else if (actorId) pendingRecordCommands.set(owner, { ...current, phase: "SAVED", result });
    } catch (error) {
      const rejected = !current.wasUnknown && !recover && error instanceof ApiError && error.status >= 400 && error.status < 500;
      if (actorId || active.current === lifetime) pendingRecordCommands.set(owner, rejected ? null : { ...current, phase: "UNKNOWN", wasUnknown: true });
      if (active.current === lifetime) onFailure({ issues: rejected && error instanceof ApiError ? error.issues : [],
        message: rejected && error instanceof ApiError ? error.message :
          "The save outcome is unknown. Check the saved result or retry this exact request before making another change." });
    } finally { if (locked.current === lifetime) locked.current = null; if (active.current === lifetime) setSending(null); }
  }
  async function submit(values: Values) {
    if (!command || locked.current === lifetime || pendingRecordCommands.get(owner)) return;
    locked.current = lifetime; setSending(lifetime); onStart();
    const { payload, ...scope } = command, submitted = { ...values };
    let prepared: PendingRecordCommand;
    try {
      prepared = { key: crypto.randomUUID(), requestHash: await resourceRequestHash(scope, payload(submitted)), scope,
        values: submitted, save, phase: "UNKNOWN", wasUnknown: false };
    } catch {
      if (active.current === lifetime) onFailure({ message: "The request could not be prepared. No save was sent. Reload this form and try again.", issues: [] });
      return;
    } finally { if (locked.current === lifetime) locked.current = null; if (active.current === lifetime) setSending(null); }
    if (active.current === lifetime) await sendPacket(prepared);
  }
  return { packet, ownPacket, busy, submit,
    retry: () => ownPacket ? sendPacket(ownPacket) : Promise.resolve(),
    recover: () => ownPacket ? sendPacket(ownPacket, true) : Promise.resolve(),
    acceptSaved: () => { if (ownPacket?.result) finish(ownPacket, ownPacket.result); },
  };
}

export type RecordCommandController = ReturnType<typeof useRecordCommand>;
