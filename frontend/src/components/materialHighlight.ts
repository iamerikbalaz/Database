export type HighlightState = { ids: Set<string>; anchor: string | null };
export function highlightMaterial(state: HighlightState, id: string, order: string[], modifiers: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }): HighlightState {
  const additive = modifiers.ctrlKey || modifiers.metaKey;
  const visible = new Set(order);
  const current = new Set([...state.ids].filter(value => visible.has(value)));
  if (modifiers.shiftKey && state.anchor && visible.has(state.anchor)) {
    const from = order.indexOf(state.anchor), to = order.indexOf(id);
    if (to < 0) return { ids: current, anchor: state.anchor };
    const range = order.slice(Math.min(from, to), Math.max(from, to) + 1);
    return { ids: new Set(additive ? [...current, ...range] : range), anchor: state.anchor };
  }
  if (additive) { if (current.has(id)) current.delete(id); else current.add(id); }
  return { ids: additive ? current : new Set([id]), anchor: id };
}

export function isInteractiveTarget(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest("a,button,input,select,textarea,label,summary,[role='combobox'],[role='button'],[contenteditable='true']"));
}
