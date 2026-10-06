import { createContext } from "react";

export interface MaterialListSnapshot {
  selectedIds: string[];
  filters: Record<string, string | string[]>;
}

function copy(snapshot: MaterialListSnapshot): MaterialListSnapshot {
  return {
    selectedIds: [...snapshot.selectedIds],
    filters: Object.fromEntries(Object.entries(snapshot.filters).map(([key, value]) => [key, Array.isArray(value) ? [...value] : value])),
  };
}

export function createMaterialListMemory() {
  const lists = new Map<string, MaterialListSnapshot>();
  return {
    read(scope: string) { const saved = lists.get(scope); return saved ? copy(saved) : undefined; },
    write(scope: string, snapshot: MaterialListSnapshot) { lists.set(scope, copy(snapshot)); },
  };
}

// Route memory belongs to the mounted signed-in app, never to browser storage.
export const MaterialListMemoryContext = createContext<ReturnType<typeof createMaterialListMemory> | null>(null);
