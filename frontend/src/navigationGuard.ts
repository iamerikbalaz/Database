import { useEffect } from "react";

const beforeNavigate = "reawote:before-navigate";
export const navigationBlocked = "reawote:navigation-blocked";
export const navigationRecoveryMessage = "A change is still being saved or its result is unknown. Stay on this page, wait for it to finish, or recover the same request before leaving.";

/** Check current request refs, including the interval before React renders busy state. */
export function useNavigationGuard(blocked: () => boolean) {
  useEffect(() => {
    const guard = (event: Event) => { if (blocked()) event.preventDefault(); };
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!blocked()) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener(beforeNavigate, guard);
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      window.removeEventListener(beforeNavigate, guard);
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, [blocked]);
}

export function requestNavigation(destination: string) {
  const permitted = window.dispatchEvent(new CustomEvent(beforeNavigate, { cancelable: true, detail: { destination } }));
  if (!permitted) window.dispatchEvent(new Event(navigationBlocked));
  return permitted;
}
