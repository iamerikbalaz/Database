/** Numbered PREVIEW images share the same order in the list and material card. */
export function orderPreviews<T extends { name: string }>(entries: readonly T[]): T[] {
  const primary = (name: string) => /^(FABRIC|SPHERE)_1\.png$/i.test(name) ? 0 : 1;
  const number = (name: string) => {
    const match = /_(\d+)\.[^.]+$/.exec(name);
    return match ? Number(match[1]) : Number.POSITIVE_INFINITY;
  };
  return [...entries].sort((left, right) => primary(left.name) - primary(right.name)
    || number(left.name) - number(right.name)
    || left.name.localeCompare(right.name, "en", { numeric: true, sensitivity: "base" })
    || left.name.localeCompare(right.name, "en"));
}
