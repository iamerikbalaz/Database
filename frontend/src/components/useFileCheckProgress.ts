import { useEffect, useRef, useState } from "react";
import type { FileCheckOptions, FileCheckProgress } from "../api/materialCheckJobs";
import { sessionGeneration } from "../auth/sessionTransport";

export function useFileCheckProgress() {
  const [progress, setProgress] = useState<FileCheckProgress | null>(null);
  const [resume, setResume] = useState<(() => void) | null>(null);
  const [running, setRunning] = useState(false);
  const controller = useRef<AbortController | null>(null), mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; controller.current?.abort(); };
  }, []);
  const begin = (): FileCheckOptions => {
    controller.current?.abort();
    const current = new AbortController(), generation = sessionGeneration(); controller.current = current;
    setProgress(null); setResume(null); setRunning(true);
    const live = () => mounted.current && controller.current === current && !current.signal.aborted && generation === sessionGeneration();
    return { signal: current.signal,
      onProgress: value => { if (live()) { setProgress(value); setResume(null); } },
      onPaused: retry => { if (live()) setResume(() => () => { if (live()) { setResume(null); retry(); } }); },
    };
  };
  const finish = () => { if (mounted.current) { setRunning(false); setResume(null); } };
  return { progress, resume, running, begin, finish };
}
