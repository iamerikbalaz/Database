import { useEffect, useRef, type ReactNode } from "react";

/** Keep one semantic table in either the page or a bounded results area. */
export function DatabaseTableViewport({ children, className = "", label, scrollMode = "page" }: { children: ReactNode; className?: string; label: string; scrollMode?: "page" | "contained" }) {
  const root = useRef<HTMLDivElement>(null), body = useRef<HTMLDivElement>(null), bar = useRef<HTMLDivElement>(null), track = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const viewport = body.current!, scrollbar = bar.current!, spacer = track.current!;
    const table = viewport.querySelector("table"), header = table?.querySelector("thead");
    if (!table || !header) return;
    if (scrollMode === "contained") {
      const measureWidths = () => {
        // The results area's vertical scrollbar makes its usable width smaller
        // than the top bar. Both scrollbars must still reach the same last column.
        spacer.style.width = `${Math.max(0, viewport.scrollWidth + scrollbar.clientWidth - viewport.clientWidth)}px`;
        if (scrollbar.scrollLeft !== viewport.scrollLeft) scrollbar.scrollLeft = viewport.scrollLeft;
      };
      const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measureWidths) : undefined;
      observer?.observe(table); observer?.observe(viewport); observer?.observe(scrollbar);
      measureWidths();
      return () => observer?.disconnect();
    }
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
    return () => { observer?.disconnect(); window.removeEventListener("scroll", schedule, true); window.removeEventListener("resize", measure); if (frame !== undefined) cancelAnimationFrame(frame); viewport.style.removeProperty("--database-header-offset"); };
  }, [children, scrollMode]);
  return <div ref={root} className={`database-table-viewport${scrollMode === "contained" ? " database-table-viewport--contained" : ""}`}>
    <div ref={bar} className="database-table-scrollbar" role="region" aria-label={`${label} horizontal scroll`} tabIndex={0}
      onScroll={event => { if (body.current && body.current.scrollLeft !== event.currentTarget.scrollLeft) body.current.scrollLeft = event.currentTarget.scrollLeft; }}><div ref={track} /></div>
    <div ref={body} className={`database-table-body ${className}`} role="region" aria-label={label} tabIndex={0}
      onScroll={event => { if (bar.current && bar.current.scrollLeft !== event.currentTarget.scrollLeft) bar.current.scrollLeft = event.currentTarget.scrollLeft; }}>{children}</div>
  </div>;
}
