import { useEffect, useState } from "react";

const workspaceQuery = "(min-width: 900px) and (min-height: 700px)";

/** Keep controls fixed only when the viewport leaves enough room for results. */
export function useDatabaseWorkspace() {
  const [available, setAvailable] = useState(() => typeof window.matchMedia !== "function" || window.matchMedia(workspaceQuery).matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(workspaceQuery), update = () => setAvailable(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return available;
}
