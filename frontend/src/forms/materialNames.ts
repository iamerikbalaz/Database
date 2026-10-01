export function pastedMaterialNames(value: string): string[] {
  if (value.includes("\t")) throw new Error("Paste one Excel column only. Multiple columns are not supported.");
  const names = value.split(/\r\n|\r|\n/).map(name => name.trim()).filter(Boolean);
  if (!names.length || names.length > 100 || names.some(name => name.length > 255)) throw new Error("Enter 1–100 names, one per line, up to 255 characters each.");
  const normalized = names.map(name => name.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toUpperCase().replace(/[^A-Z0-9.]+/g, "-").replace(/^[-.]+|[-.]+$/g, ""));
  if (normalized.some(name => !/[A-Z0-9]/.test(name))) throw new Error("Each name needs letters or numbers.");
  if (new Set(normalized).size !== names.length) throw new Error("The list contains duplicate names after normalization. Remove duplicates before creating materials.");
  return names;
}
