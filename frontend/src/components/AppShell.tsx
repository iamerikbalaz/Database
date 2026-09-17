import { useRef, useState, type MouseEvent, type ReactNode } from "react";
import { Icon } from "./Icon";
import { useSession } from "../auth/context";

const items = [
  ["Dashboard", "/dashboard", "dashboard"],
  ["Companies", "/companies", "companies"],
  ["Projects", "/projects", "projects"],
  ["Materials", "/materials", "materials"],
  ["Compare", "/compare", "materials"],
  ["Catalog", "/catalog", "materials"],
  ["Imports", "/imports", "materials"],
  ["Publication", "/publication", "publication"],
  ["Settings", "/settings", "settings"],
];
export function AppShell({
  currentPath,
  navigate,
  children,
}: {
  currentPath: string;
  navigate: (path: string) => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const account = useSession();
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = () => dialog.current?.close();
  const go = (event: MouseEvent<HTMLAnchorElement>, path: string) => {
    if (
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    event.preventDefault();
    navigate(path);
    if (dialog.current?.open) close();
  };
  const navigation = (
    <>
      <a
        className="brand"
        href="/dashboard"
        onClick={(e) => go(e, "/dashboard")}
        aria-label="REAWOTE dashboard"
      >
        <span className="brand-mark">R</span>
        <span>REAWOTE</span>
      </a>
      <nav aria-label="Main navigation">
        {items.filter(([, href]) => href !== "/imports" || account?.session.user.role === "ADMIN").map(([label, href, icon]) => {
          const active =
            currentPath === href ||
            (href === "/dashboard" && currentPath === "/") ||
            currentPath.startsWith(href + "/");
          return (
            <a
              key={href}
              href={href}
              onClick={(e) => go(e, href)}
              className={active ? "active" : ""}
              aria-current={active ? "page" : undefined}
            >
              <Icon name={icon} />
              <span>{label}</span>
            </a>
          );
        })}
      </nav>
      <div className="sidebar-footer">
        <span>Internal workspace</span>
      </div>
    </>
  );
  return (
    <div className="app-shell">
      <aside className="sidebar desktop-sidebar">{navigation}</aside>
      <dialog
        ref={dialog}
        id="mobile-navigation"
        className="mobile-navigation"
        aria-label="Navigation"
        onCancel={(event) => {
          event.preventDefault();
          close();
        }}
        onClose={() => {
          setOpen(false);
          trigger.current?.focus();
        }}
      >
        <div className="sidebar">
          <button className="navigation-close" onClick={close}>
            Close navigation
          </button>
          {navigation}
        </div>
      </dialog>
      <div className="workspace">
        <header className="topbar">
          <button
            ref={trigger}
            className="menu-button"
            aria-label="Open navigation"
            aria-expanded={open}
            aria-controls="mobile-navigation"
            onClick={() => {
              dialog.current?.showModal();
              setOpen(true);
            }}
          >
            <Icon name="menu" />
          </button>
          <div className="environment">
            <span />
            Internal workspace
          </div>
          {account ? <details className="account-menu">
            <summary aria-label="User menu">{account.session.user.display_name}</summary>
            <div className="account-menu-items">
              <span>{account.session.user.email}</span>
              <button className="button" disabled={account.pending} onClick={account.changePassword}>Change password</button>
              <button className="button" disabled={account.pending} onClick={account.logout}>Sign out</button>
            </div>
          </details> : <button
            disabled
            className="user-button"
            aria-label="User menu"
            title="Coming later"
          >
            User
          </button>}
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
