import type { CSSProperties, SelectHTMLAttributes } from "react";
import type { ChoiceColors, ChoiceTone } from "../data/choiceColors";
import "./ColoredSelect.css";

const palette: Record<ChoiceTone, CSSProperties> = {
  default: { backgroundColor: "#f1f1ef", color: "#51514e" },
  brown: { backgroundColor: "#eee0da", color: "#754f3d" },
  orange: { backgroundColor: "#fadec9", color: "#874910" },
  yellow: { backgroundColor: "#fdecc8", color: "#795d12" },
  green: { backgroundColor: "#dbeddb", color: "#285d39" },
  blue: { backgroundColor: "#d3e5ef", color: "#255b7c" },
  purple: { backgroundColor: "#e8deee", color: "#654583" },
  pink: { backgroundColor: "#f5e0e9", color: "#873d65" },
  red: { backgroundColor: "#ffe2dd", color: "#9b352c" },
};
type Option = { value: string; label: string; disabled?: boolean };
type Props = Omit<SelectHTMLAttributes<HTMLSelectElement>, "value" | "children"> & {
  value: string; options: readonly Option[]; colors?: ChoiceColors;
};

/** Native selection and keyboard behavior; OS menus may use their own highlight color. */
export function ColoredSelect({ value, options, colors, className, style, ...props }: Props) {
  const tone = colors?.[value] ?? "default";
  return <select {...props} value={value} className={[className, colors && "colored-select"].filter(Boolean).join(" ") || undefined}
    data-choice-color={colors ? tone : undefined} style={colors ? { ...palette[tone], ...style } : style}>
    {options.map(option => <option key={option.value} value={option.value} disabled={option.disabled}
      data-choice-color={colors ? colors[option.value] ?? "default" : undefined}
      style={colors ? palette[colors[option.value] ?? "default"] : undefined}>{option.label}</option>)}
  </select>;
}

export function ColoredValue({ value, label = value, colors }: { value: string; label?: string; colors: ChoiceColors }) {
  const tone = colors[value] ?? "default";
  return <span className="colored-value" data-choice-color={tone} style={palette[tone]}>{label}</span>;
}
