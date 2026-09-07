import type { AnchorHTMLAttributes, MouseEvent } from "react";

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
