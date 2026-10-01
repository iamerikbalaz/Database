import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import "./ResponsiveFilters.css";

export type PriorityFilter = { key: string; width: number; active?: boolean; content: ReactNode };
const gap = 10, clearWidth = 106, moreWidth = 132, panelPadding = 12, rowHeight = 64;

function visibleCount(filters: PriorityFilter[], width: number, controlsWidth: number) {
  if (!width || filters.reduce((sum, field) => sum + field.width + gap, controlsWidth) <= width) return filters.length;
  let used = controlsWidth + moreWidth + gap, count = 0;
  for (const field of filters) {
    if (used + field.width + gap > width) break;
    used += field.width + gap; count++;
  }
  return Math.max(1, count);
}

/** Resize the presentation without remounting filter inputs or changing their values. */
export function ResponsiveFilters({ filters, compact, disabled, label, onClear, search = false, keepFilters, onKeepFiltersChange }: {
  filters: PriorityFilter[]; compact: boolean; disabled?: boolean; label: string; onClear: () => void; search?: boolean;
  keepFilters?: boolean; onKeepFiltersChange?: (keep: boolean) => void;
}) {
  const root = useRef<HTMLFieldSetElement>(null), track = useRef<HTMLDivElement>(null), toggle = useRef<HTMLButtonElement>(null);
  const latestFilters = useRef(filters), focusOverflow = useRef(false);
  const controlsWidth = clearWidth, latestControlsWidth = useRef(controlsWidth);
  const id = useId(), [width, setWidth] = useState(0), [open, setOpen] = useState(false);
  const count = compact ? visibleCount(filters, width, controlsWidth) : filters.length;
  const overflow = filters.slice(count), expanded = open && overflow.length > 0;
  const activeCount = overflow.filter(field => field.active).length;
  const panelWidth = Math.min(width || 620, 620), columns = panelWidth >= 520 ? 3 : panelWidth >= 340 ? 2 : 1;
  const fieldWidth = (panelWidth - panelPadding * 2 - gap * (columns - 1)) / columns;
  useLayoutEffect(() => { latestFilters.current = filters; latestControlsWidth.current = controlsWidth; }, [filters, controlsWidth]);
  useLayoutEffect(() => {
    if (!expanded || !focusOverflow.current) return;
    focusOverflow.current = false;
    root.current?.querySelector<HTMLElement>(".priority-filter--overflow input, .priority-filter--overflow select, .priority-filter--overflow button")?.focus();
  }, [expanded]);
  useLayoutEffect(() => {
    const element = track.current;
    if (!element) return;
    const measure = (next: number) => {
      if (!next) return;
      // Keep a focused filter accessible if a resize moves it into the overflow.
      const focused = document.activeElement?.closest(".priority-filter");
      const index = focused ? Array.from(element.querySelectorAll(".priority-filter")).indexOf(focused) : -1;
      if (index >= visibleCount(latestFilters.current, next, latestControlsWidth.current)) setOpen(true);
      setWidth(next);
    };
    measure(element.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(entries => { if (entries[0]) measure(entries[0].contentRect.width); });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!expanded) return;
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false); };
    window.addEventListener("pointerdown", outside);
    return () => window.removeEventListener("pointerdown", outside);
  }, [expanded]);
  return <fieldset ref={root} className={`material-filters database-filters priority-filters${expanded ? " priority-filters--expanded" : ""}`}
    disabled={disabled} role={search ? "search" : undefined} aria-label={search ? label : undefined}
    onKeyDown={event => { if (event.key === "Escape" && expanded) { event.preventDefault(); setOpen(false); toggle.current?.focus(); } }}
    onBlur={event => { if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <legend className="sr-only">{label}</legend>
    <div className="priority-filters-track" ref={track}>
      {filters.map((field, index) => {
        const extra = index >= count, extraIndex = index - count;
        const style = {
          "--filter-width": `${extra ? fieldWidth : field.width}px`,
          ...(extra ? { "--filter-left": `${panelWidth - panelPadding - extraIndex % columns * (fieldWidth + gap)}px`, "--filter-top": `${18 + Math.floor(extraIndex / columns) * rowHeight}px` } : {}),
        } as CSSProperties;
        return <div id={`${id}-${field.key}`} key={field.key} style={style} hidden={extra && !expanded}
          className={`priority-filter${extra ? " priority-filter--overflow" : ""}`}>{field.content}</div>;
      })}
      <div className="priority-filters-actions">{overflow.length > 0 && <button ref={toggle} type="button" className="button priority-filters-more" aria-expanded={expanded}
        aria-controls={overflow.map(field => `${id}-${field.key}`).join(" ")} onClick={() => { focusOverflow.current = !expanded; setOpen(!expanded); }}>
        More filters{activeCount ? ` (${activeCount})` : ""}<span aria-hidden="true">{expanded ? "▴" : "▾"}</span>
      </button>}
      <div className="priority-filters-clear-group">
        {onKeepFiltersChange && <label className="priority-filters-keep"><span>Keep filters</span><input type="checkbox" checked={keepFilters ?? false} onChange={event => onKeepFiltersChange(event.target.checked)} /></label>}
        <button type="button" className="button priority-filters-clear" onClick={onClear}>Clear filters</button>
      </div>
      </div>
    </div>
    {expanded && <div className="priority-filters-backdrop" aria-hidden="true" style={{ width: panelWidth, height: Math.ceil(overflow.length / columns) * rowHeight + panelPadding * 2 - gap }} />}
  </fieldset>;
}
