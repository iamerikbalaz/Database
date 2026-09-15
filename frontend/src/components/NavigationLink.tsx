import type { AnchorHTMLAttributes, MouseEvent } from "react";
import { useSession } from "../auth/context";
import { restrictedDestination } from "../auth/permissions";

interface NavigationLinkProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  href: string;
  navigate: (path: string) => void;
}

export function NavigationLink({
  href,
  navigate,
  onClick,
  ...props
}: NavigationLinkProps) {
  const role = useSession()?.session.user.role;
  if (restrictedDestination(href, role)) return null;
  function handleClick(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      props.target === "_blank" ||
      props.download
    )
      return;
    event.preventDefault();
    navigate(href);
  }
  return <a {...props} href={href} onClick={handleClick} />;
}
