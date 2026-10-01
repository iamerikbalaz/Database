import { useEffect, useState, type SetStateAction } from "react";
import { useSession } from "../auth/context";

type FilterValue = string | boolean | number | string[];
type Filters = Record<string, FilterValue>;
type SavedFilters<T> = { key: string | null; filters: T; keep: boolean };

function loadFilters<T extends Filters>(key: string | null, defaults: T): SavedFilters<T> {
  const initial = { key, filters: { ...defaults }, keep: false };
  if (!key) return initial;
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(key) ?? "null");
    if (!saved || typeof saved !== "object" || !("keep" in saved) || saved.keep !== true || !("filters" in saved) || !saved.filters || typeof saved.filters !== "object") return initial;
    const values = saved.filters as Record<string, unknown>;
    for (const [field, fallback] of Object.entries(defaults)) {
      const value = values[field];
      if (Array.isArray(fallback) ? Array.isArray(value) && value.every(item => typeof item === "string") : typeof value === typeof fallback && (typeof value !== "number" || Number.isFinite(value))) {
        initial.filters[field as keyof T] = value as T[keyof T];
      }
    }
    return { ...initial, keep: true };
  } catch { return initial; }
}

/** Opt-in preferences belong to the signed-in user and this database, never to another session. */
export function useDatabaseFilters<T extends Filters>(scope: string, defaults: T) {
  const userId = useSession()?.session.user.id;
  const key = userId ? `reawote-filters:${encodeURIComponent(userId)}:${scope}:v1` : null;
  const [stored, setStored] = useState(() => loadFilters(key, defaults));
  const state = stored.key === key ? stored : loadFilters(key, defaults);
  if (stored.key !== key) setStored(state);
  useEffect(() => {
    if (!state.key) return;
    try {
      if (state.keep) localStorage.setItem(state.key, JSON.stringify({ keep: true, filters: state.filters }));
      else localStorage.removeItem(state.key);
    } catch { /* Storage may be disabled; the current filters still work. */ }
  }, [state]);
  const setFilters = (next: SetStateAction<T>) => setStored(previous => {
    const current = previous.key === key ? previous : loadFilters(key, defaults);
    return { ...current, filters: typeof next === "function" ? next(current.filters) : next };
  });
  return {
    filters: state.filters, setFilters, keepFilters: state.keep,
    setKeepFilters: (keep: boolean) => setStored(previous => ({ ...(previous.key === key ? previous : loadFilters(key, defaults)), keep })),
    resetFilters: () => setFilters({ ...defaults }),
  };
}

export type DatabaseSort = "created-desc" | "created-asc" | "name-asc" | "name-desc" | "number-asc" | "number-desc";
export const databaseSortOptions: { value: DatabaseSort; label: string }[] = [
  { value: "created-desc", label: "Created: newest first" },
  { value: "created-asc", label: "Created: oldest first" },
  { value: "name-asc", label: "Name: A–Z" },
  { value: "name-desc", label: "Name: Z–A" },
];
export const numberedDatabaseSortOptions = [...databaseSortOptions,
  { value: "number-asc", label: "Number: ascending" }, { value: "number-desc", label: "Number: descending" }];

export function sortDatabaseRecords<T extends { id: string }>(records: T[], sort: string, name: (record: T) => string, created: (record: T) => string | null | undefined, number?: (record: T) => number | string): T[] {
  return [...records].sort((a, b) => {
    let order: number;
    if (number && (sort === "number-asc" || sort === "number-desc")) order = String(number(a)).localeCompare(String(number(b)), undefined, { numeric: true }) * (sort === "number-desc" ? -1 : 1);
    else if (sort === "name-asc" || sort === "name-desc") order = name(a).localeCompare(name(b), undefined, { numeric: true, sensitivity: "base" }) * (sort === "name-desc" ? -1 : 1);
    else {
      const first = Date.parse(created(a) ?? ""), second = Date.parse(created(b) ?? "");
      if (!Number.isFinite(first) || !Number.isFinite(second)) order = Number.isFinite(first) ? -1 : Number.isFinite(second) ? 1 : 0;
      else order = (first - second) * (sort === "created-asc" ? 1 : -1);
    }
    return order || a.id.localeCompare(b.id);
  });
}
