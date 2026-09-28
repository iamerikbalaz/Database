// REAWOTE texture gallery filter, https://reawote.com/textures, verified 2026-09-28.
export const materialColors = ["#CCCC99", "#009999", "#FFFFFF", "#FF0000", "#0099FF", "#9900CC", "#00CC00", "#0000FF", "#660000", "#000000", "#996633", "#FFFF00", "#FF0099", "#663300", "#999999", "#333333", "#FF822D", "#666666"];

// The platform publishes HEX values; these descriptive labels make the same
// swatches understandable without requiring users to remember a HEX code.
const colorNames: Record<string, string> = {
  "#CCCC99": "Beige", "#009999": "Turquoise", "#FFFFFF": "White", "#FF0000": "Red",
  "#0099FF": "Light blue", "#9900CC": "Purple", "#00CC00": "Green", "#0000FF": "Blue",
  "#660000": "Burgundy", "#000000": "Black", "#996633": "Brown", "#FFFF00": "Yellow",
  "#FF0099": "Pink", "#663300": "Dark brown", "#999999": "Light gray", "#333333": "Charcoal",
  "#FF822D": "Orange", "#666666": "Gray",
};
export function materialColorLabel(color: string) { return colorNames[color.toUpperCase()] ?? `Custom ${color.toUpperCase()}`; }
