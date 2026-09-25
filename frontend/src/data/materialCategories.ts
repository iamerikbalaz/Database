import data from "./materialCategories.json";

export const materialCategories = data;
export const categoryLabel = (code: string) => {
  const category = data.find(item => item.code === code);
  return category ? `${category.code} · ${category.value}` : `${code} · Legacy category`;
};
export const catalogCategoryLabel = (value: string) => {
  const category = data.find(item => item.value === value);
  return category ? `${category.code} · ${value}` : value;
};
