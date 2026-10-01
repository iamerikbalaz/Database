import data from "./materialCategories.json";
import type { CatalogValue } from "../api/catalogClient";

export const materialCategories = data;
export const catalogMaterialCategoryLabels = (values: CatalogValue[]) => values.map(item => ({ code: item.abbreviation ?? item.aliases?.[0] ?? "", value: item.value, aliases: item.aliases }));
export const catalogMaterialCategories = (values: CatalogValue[]) => values.filter(item => item.active && item.abbreviation && /^[A-Z0-9-]+$/.test(item.abbreviation))
  .map(item => ({ code: item.abbreviation!, value: item.value, aliases: item.aliases })).sort((a, b) => a.code.localeCompare(b.code, undefined, { numeric: true }));
export const categoryLabel = (code: string, options: { code: string; value: string; aliases?: string[] }[] = data) => {
  const category = options.find(item => item.code === code || item.aliases?.includes(code)) ?? data.find(item => item.code === code);
  return category ? `${code} · ${category.value}` : `${code} · Legacy category`;
};
export const catalogCategoryLabel = (value: string) => {
  const category = data.find(item => item.value === value);
  return category ? `${category.code} · ${value}` : value;
};
