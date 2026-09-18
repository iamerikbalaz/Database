import type { CommandScope } from "../api/resourceCommandClient";
import type { Values } from "./fields";

export type SavedRecord = { path: string; message: string };
export interface PendingRecordCommand {
  key: string; requestHash: string; scope: CommandScope; values: Values;
  save: (values: Values, key?: string) => Promise<SavedRecord>;
  phase: "SENDING" | "UNKNOWN" | "SAVED";
  wasUnknown: boolean;
  result?: SavedRecord;
}
type Owner = string | symbol;
const commands = new Map<Owner, PendingRecordCommand>();
const listeners = new Set<() => void>();
const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
export const pendingRecordCommands = {
  get(owner: Owner) { return commands.get(owner) ?? null; },
  set(owner: Owner, packet: PendingRecordCommand | null) {
    const hadPending = commands.size > 0;
    if (packet) commands.set(owner, packet); else commands.delete(owner);
    if (!hadPending && commands.size) window.addEventListener("beforeunload", warn);
    if (hadPending && !commands.size) window.removeEventListener("beforeunload", warn);
    for (const notify of [...listeners]) notify();
  },
  subscribe(notify: () => void) { listeners.add(notify); return () => { listeners.delete(notify); }; },
};
