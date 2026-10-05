export type ChoiceTone = "default" | "brown" | "orange" | "yellow" | "green" | "blue" | "purple" | "pink" | "red";
export type ChoiceColors = Readonly<Record<string, ChoiceTone>>;

export const materialStatusColors: ChoiceColors = { IN_PROGRESS: "yellow", DONE: "green" };
export const materialCheckedColors: ChoiceColors = { no: "default", OK: "green", Correction: "red" };

// Read-only schema snapshot, 2026-10-05, Orders database
// 123fa58c-5edd-47f3-b7d4-d9774486a39c / data source dcfda230-c4f7-4207-817a-07643eaf4dbd.
// Notion exposes named colors, not theme-specific RGB values.
export const orderStatusColors: ChoiceColors = {
  "Not started": "default", "Price offer sent": "brown", "Waiting for samples": "red",
  "Samples Obtained": "orange", Scanned: "pink", "Post-production": "purple", Visualize: "blue",
  "Test complete": "brown", Ongoing: "yellow", "To be invoiced": "green", invoiced: "red",
  Done: "green", Canceled: "brown",
};
export const orderPriorityColors: ChoiceColors = { Low: "green", Medium: "yellow", High: "orange", Urgent: "red" };
