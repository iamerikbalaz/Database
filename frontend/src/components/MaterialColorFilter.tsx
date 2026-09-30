import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { materialColorLabel, materialColors } from "../data/materialColors";

export function MaterialColorFilter({ value, onChange, disabled = false }: { value: string[]; onChange: (value: string[]) => void; disabled?: boolean }) {
  const id = useId(), root = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false), [active, setActive] = useState(0);
  const search = useRef({ value: "", at: 0 });
  const colors = [...materialColors, ...value.filter(color => !materialColors.includes(color))];
  const expanded = open && !disabled;
  const toggle = (color: string) => onChange(value.includes(color) ? value.filter(item => item !== color) : [...value, color]);
  useEffect(() => {
    if (!expanded) return;
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false); };
    window.addEventListener("pointerdown", outside);
    return () => window.removeEventListener("pointerdown", outside);
  }, [expanded]);
  useEffect(() => { if (expanded) document.getElementById(`${id}-color-${active}`)?.scrollIntoView?.({ block: "nearest" }); }, [id, active, expanded]);
  const keyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Tab") { setOpen(false); return; }
    if (event.key === "Escape") { if (expanded) { event.preventDefault(); event.stopPropagation(); setOpen(false); } return; }
    if (["ArrowDown", "ArrowUp", "Home", "End", "Enter", " "].includes(event.key)) {
      event.preventDefault();
      if (event.key === "Enter" || event.key === " ") { if (expanded) toggle(colors[active]); else setOpen(true); }
      else { setOpen(true); setActive(index => event.key === "Home" ? 0 : event.key === "End" ? colors.length - 1 : Math.max(0, Math.min(colors.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))); }
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now(), query = (now - search.current.at < 700 ? search.current.value : "") + event.key.toLowerCase();
      search.current = { value: query, at: now };
      const index = colors.findIndex(color => materialColorLabel(color).toLowerCase().startsWith(query));
      if (index >= 0) { event.preventDefault(); setActive(index); setOpen(true); }
    }
  };
  const swatch = (color: string) => <span className="material-color-swatch" style={{ backgroundColor: color }} aria-hidden="true" />;
  return <div className="material-color-field material-color-filter" ref={root} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <label htmlFor={`${id}-button`}>Color</label>
    <button id={`${id}-button`} type="button" className="material-color-select" role="combobox" aria-label="Color" aria-haspopup="listbox"
      aria-expanded={expanded} aria-controls={expanded ? `${id}-options` : undefined} aria-activedescendant={expanded ? `${id}-color-${active}` : undefined}
      disabled={disabled} onClick={() => setOpen(!expanded)} onKeyDown={keyDown}>
      {value.slice(0, 3).map(color => <span key={color} className="material-color-filter-chip">{swatch(color)}</span>)}
      <span className="material-color-value">{value.length === 0 ? "All colors" : value.length === 1 ? materialColorLabel(value[0]) : `${value.length} colors`}</span><span className="material-color-chevron" aria-hidden="true">⌄</span>
    </button>
    {expanded && <div className="material-color-options">
      <div role="listbox" id={`${id}-options`} aria-label="Material colors" aria-multiselectable="true">
        {colors.map((color, index) => <div key={color} id={`${id}-color-${index}`} role="option" aria-selected={value.includes(color)}
          className={`material-color-option${active === index ? " material-color-option--active" : ""}`} title={color} onPointerMove={() => setActive(index)}
          onMouseDown={event => event.preventDefault()} onClick={() => toggle(color)}>
          {swatch(color)}<span>{materialColorLabel(color)}</span><span className="material-color-code">{color}</span>{value.includes(color) && <span className="material-color-check" aria-hidden="true">✓</span>}
        </div>)}
      </div>
      <button type="button" className="button material-color-filter-clear" disabled={!value.length} onClick={() => onChange([])}>All colors</button>
    </div>}
  </div>;
}
