import { useEffect, useRef, type ReactNode } from "react";

/** Keep a single semantic table and its native horizontal scrolling. The header
 * follows page scrolling; rows never live in a fixed-height vertical viewport. */
export function DatabaseTableViewport({ children, className = "", label }: { children: ReactNode; className?: string; label: string }) {
  const root = useRef<HTMLDivElement>(null), body = useRef<HTMLDivElement>(null), bar = useRef<HTMLDivElement>(null), track = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const viewport = body.current!, scrollbar = bar.current!, spacer = track.current!;
    const table = viewport.querySelector("table"), header = table?.querySelector("thead");
    if (!table || !header) return;
    const position = () => {
      const offset = Math.max(0, Math.min(scrollbar.getBoundingClientRect().bottom - table.getBoundingClientRect().top, table.offsetHeight - header.offsetHeight));
      viewport.style.setProperty("--database-header-offset", `${offset}px`);
    };
    const measure = () => { spacer.style.width = `${table.scrollWidth}px`; scrollbar.scrollLeft = viewport.scrollLeft; position(); };
    let frame: number | undefined;
    const schedule = () => { if (frame === undefined) frame = requestAnimationFrame(() => { frame = undefined; position(); }); };
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : undefined;
    observer?.observe(table); observer?.observe(root.current!);
    window.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", measure);
    measure();
    return () => { observer?.disconnect(); window.removeEventListener("scroll", schedule, true); window.removeEventListener("resize", measure); if (frame !== undefined) cancelAnimationFrame(frame); };
  }, [children]);
  return <div ref={root} className="database-table-viewport">
    <div ref={bar} className="database-table-scrollbar" role="region" aria-label={`${label} horizontal scroll`} tabIndex={0}
      onScroll={event => { if (body.current) body.current.scrollLeft = event.currentTarget.scrollLeft; }}><div ref={track} /></div>
    <div ref={body} className={`database-table-body ${className}`} role="region" aria-label={label} tabIndex={0}
      onScroll={event => { if (bar.current) bar.current.scrollLeft = event.currentTarget.scrollLeft; }}>{children}</div>
  </div>;
}
