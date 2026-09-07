import { useEffect, useState } from "react";

// Each request owns its result; late responses cannot overwrite a newer page or retry.
export function useResource<T>(load: () => Promise<T>) {
 const [attempt, setAttempt] = useState(0);
 const [result, setResult] = useState<{ load: typeof load; attempt: number; data?: T; error: boolean }>();
 useEffect(() => {
  let active = true;
  void Promise.resolve().then(load).then(
   data => { if (active) setResult({ load, attempt, data, error: false }); },
   () => { if (active) setResult({ load, attempt, error: true }); },
  );
  return () => { active = false; };
 }, [load, attempt]);
 const current = result?.load === load && result.attempt === attempt ? result : undefined;
 return { data: current?.data, error: current?.error ?? false, retry: () => setAttempt(n => n + 1) };
}
