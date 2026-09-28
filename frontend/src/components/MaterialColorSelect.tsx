import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { materialColors } from "../data/materialColors";

export function MaterialColorSelect({ value, onChange, disabled = false }: { value: string; onChange: (value: string) => void; disabled?: boolean }) {
  const id = useId(), button = useRef<HTMLButtonElement>(null), root = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false), [active, setActive] = useState(0);
  const search = useRef({ value: "", at: 0 });
  const values = ["", ...(value && !materialColors.includes(value) ? [value] : []), ...materialColors];
  const label = (color: string) => color ? `${color}${materialColors.includes(color) ? "" : " (recorded value)"}` : "No color";
  const expanded = open && !disabled;
  const reveal = (index = values.indexOf(value)) => { setActive(Math.max(0, index)); setOpen(true); };
  const select = (index: number) => { onChange(values[index]); setOpen(false); button.current?.focus(); };
  useEffect(() => {
    if (!expanded) return;
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false); };
    window.addEventListener("pointerdown", outside);
    return () => window.removeEventListener("pointerdown", outside);
  }, [expanded]);
  useEffect(() => {
    if (expanded) document.getElementById(`${id}-option-${active}`)?.scrollIntoView?.({ block: "nearest" });
  }, [active, expanded, id]);
  const keyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Tab") { setOpen(false); return; }
    if (event.key === "Escape") { if (expanded) { event.preventDefault(); event.stopPropagation(); setOpen(false); } return; }
    if (["ArrowDown", "ArrowUp", "Home", "End", "Enter", " "].includes(event.key)) {
      event.preventDefault();
      if (event.key === "Enter" || event.key === " ") { if (expanded) select(active); else reveal(); }
      else if (event.key === "Home") reveal(0);
      else if (event.key === "End") reveal(values.length - 1);
      else if (!expanded) reveal();
      else setActive(index => Math.max(0, Math.min(values.length - 1, index + (event.key === "ArrowDown" ? 1 : -1))));
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now();
      const query = (now - search.current.at < 700 ? search.current.value : "") + event.key.toUpperCase();
      search.current = { value: query, at: now };
      const match = values.findIndex(color => label(color).toUpperCase().startsWith(query) || color.replace(/^#/, "").startsWith(query));
      if (match >= 0) { event.preventDefault(); reveal(match); }
    }
  };
  const swatch = (color: string) => <span className={`material-color-swatch${color ? "" : " material-color-swatch--empty"}`} style={color ? { backgroundColor: color } : undefined} aria-hidden="true" />;
  return <div className="material-color-field" ref={root} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <label htmlFor={`${id}-button`}>Color HEX</label>
    <button id={`${id}-button`} ref={button} type="button" className="material-color-select" role="combobox" aria-label="Color HEX"
      aria-haspopup="listbox" aria-expanded={expanded} aria-controls={expanded ? `${id}-options` : undefined}
      aria-activedescendant={expanded ? `${id}-option-${active}` : undefined} disabled={disabled}
      onClick={() => expanded ? setOpen(false) : reveal()} onKeyDown={keyDown}>
      {swatch(value)}<span>{label(value)}</span><span className="material-color-chevron" aria-hidden="true">⌄</span>
    </button>
    {expanded && <div id={`${id}-options`} role="listbox" aria-label="Color HEX choices" className="material-color-options">
      {values.map((color, index) => <div id={`${id}-option-${index}`} key={color || "none"} role="option" aria-selected={value === color}
        className={`material-color-option${active === index ? " material-color-option--active" : ""}`} onPointerMove={() => setActive(index)}
        onMouseDown={event => event.preventDefault()} onClick={() => select(index)}>
        {swatch(color)}<span>{label(color)}</span>{value === color && <span className="material-color-check" aria-hidden="true">✓</span>}
      </div>)}
    </div>}
  </div>;
}
