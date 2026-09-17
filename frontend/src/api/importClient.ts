import { request } from "./client";
import { boolean, record, uuid } from "./dto";
import { ApiError } from "./errors";

export type ImportField = "identity" | "name" | "project" | "brand" | "processor";
export type ImportGroup = "project" | "brand" | "processor";
export type ImportColumns = Record<ImportField, string>;
export interface ImportSource { format: "CSV" | "XLSX"; data: string; delimiter?: "," | ";"; sheet?: string; }
export interface ImportLinks { projects: Record<string, string>; brands: Record<string, string>; processors: Record<string, string>; }
export interface ImportPlanRequest { source: ImportSource; columns: ImportColumns; links: ImportLinks; }
export interface ImportConfirmation extends ImportPlanRequest {
  idempotency_key: string; expected_preview_hash: string; acknowledge_unverified: true; reason: string;
}
function text(value: unknown, max = 2048): string {
  if (typeof value !== "string" || value.length > max) throw new Error("Invalid import text");
  return value;
}
function integer(value: unknown, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) throw new Error("Invalid import count");
  return value;
}
function list<T>(value: unknown, max: number, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length > max) throw new Error("Invalid import list");
  return value.map(parse);
}
function hash(value: unknown): string {
  const result = text(value, 64);
  if (!/^[a-f0-9]{64}$/.test(result)) throw new Error("Invalid import digest");
  return result;
}
function unique(values: string[]) {
  if (new Set(values).size !== values.length) throw new Error("Duplicate import values");
  return values;
}
function format(value: unknown): "CSV" | "XLSX" {
  if (value !== "CSV" && value !== "XLSX") throw new Error("Invalid import format");
  return value;
}
function finding(value: unknown) {
  const data = record(value); const code = text(data.code, 100); const field = text(data.field, 20);
  if (!/^IMPORT_[A-Z0-9_]+$/.test(code) || !["identity", "name", "project", "brand", "processor"].includes(field)) throw new Error("Invalid import finding");
  return { row: integer(data.row, 1, 4194304), field, code };
}
const findingMessages: Record<string, string> = {
  IMPORT_IDENTITY_FORMAT: "Use an exact identity with a four-digit number and one folder name.",
  IMPORT_CATEGORY_FORMAT: "The identity must end with its uppercase category code.",
  IMPORT_MATERIAL_NAME: "Enter a material name of 1–255 characters.",
  IMPORT_REFERENCE_UNMAPPED: "Choose the existing record for this source label.",
  IMPORT_DUPLICATE_IDENTITY_OR_NUMBER: "This file uses the same brand number more than once.",
  IMPORT_PROJECT_MISSING: "The selected project is unavailable.", IMPORT_BRAND_MISSING: "The selected brand is unavailable.",
  IMPORT_BRAND_INACTIVE: "The selected brand is inactive.", IMPORT_BRAND_PREFIX_MISMATCH: "The identity prefix does not match the selected brand.",
  IMPORT_BRAND_OPERATION_ACTIVE: "Finish or reconcile the brand's identity operation first.",
  IMPORT_PROCESSOR_UNAVAILABLE: "Choose an active processor.", IMPORT_COMPANY_UNAVAILABLE: "The associated company is unavailable or inactive.",
  IMPORT_MATERIAL_EXISTS: "A material already uses this identity or brand number.", IMPORT_NUMBER_RESERVED: "This brand number is permanently reserved.",
};
export const importFindingMessage = (code: string): string => Object.hasOwn(findingMessages, code)
  ? findingMessages[code] : "This row needs correction before import.";

const sourceMessages: Record<string, string> = {
  IMPORT_FILE_SIZE: "Choose a nonempty source file up to 4 MiB.", IMPORT_REQUEST_SIZE: "The upload is too large or incomplete. Select the file again.",
  IMPORT_UTF8_REQUIRED: "Save the CSV as UTF-8 and select it again.", IMPORT_CSV_INVALID: "Correct CSV quoting and verify the selected delimiter.",
  IMPORT_FORMULA: "Replace spreadsheet formulas or formula-prefixed values with reviewed literal values.",
  IMPORT_ACTIVE_CONTENT: "Remove macros, embedded objects and other active workbook content.", IMPORT_EXTERNAL_LINK: "Remove external workbook links.",
  IMPORT_CELL_CONTROL: "Remove invisible control characters from this cell.", IMPORT_CELL_SIZE: "Limit each cell to 2,048 characters.",
  IMPORT_COLUMN_LIMIT: "Use between 1 and 32 source columns.", IMPORT_ROW_LIMIT: "Split the source into batches of at most 2,000 data records.",
  IMPORT_ROW_WIDTH: "Every CSV record must have the same number of columns as the header.", IMPORT_HEADER: "Use nonempty, single-line column headers.",
  IMPORT_DUPLICATE_HEADER: "Give each column a distinct header.", IMPORT_EMPTY_TABLE: "The selected source has no material records.",
  IMPORT_COLUMN_MAPPING: "Select five distinct columns that exist in the source.", IMPORT_REFERENCE_LABEL: "Correct blank or multiline project, brand or processor labels.",
  IMPORT_REFERENCE_LIMIT: "Split the source so each reference group has at most 64 distinct labels.",
  IMPORT_SHEET_REQUIRED: "Select the worksheet that contains the material table.", IMPORT_SHEET_LIMIT: "Use a workbook with at most 16 worksheets.",
  IMPORT_XLSX_CELL: "Correct this workbook cell or save the reviewed literal table as UTF-8 CSV.",
  IMPORT_XLSX_INVALID: "The file is not a readable XLSX workbook. Verify its format or save a reviewed CSV copy.",
  IMPORT_XLSX_STRUCTURE: "The workbook structure is unsupported. Remove merged table cells or save the reviewed table as CSV.",
  IMPORT_XML_LIMIT: "The workbook structure exceeds the import limits. Use a smaller plain table.",
  IMPORT_ZIP_LIMIT: "The expanded workbook exceeds the import limits. Use a smaller plain table.", IMPORT_ZIP_ENTRY: "The workbook contains an unsupported package entry.",
  IMPORT_BUSY: "Two imports are already being processed. Retry shortly.", IMPORT_UPLOAD_TIMEOUT: "The upload timed out. Retry the same file.",
  IMPORT_UPLOAD_DISCONNECTED: "The upload was interrupted. Select the file again.",
  IMPORT_PREVIEW_CHANGED: "The file, mapping or referenced records changed. Prepare a new preview before confirming.",
  IMPORT_BLOCKED: "The import is blocked. Prepare a new preview to review all current conflicts.",
  IMPORT_IDEMPOTENCY_CONFLICT: "This request was already used with different input. Check import history before preparing another batch.",
  IMPORT_DATABASE_CONFLICT: "The import could not be committed. Prepare a new preview to check current number reservations.",
};
export function importFailureMessage(cause: unknown, confirming = false) {
  if (cause instanceof ApiError && cause.code && Object.hasOwn(sourceMessages, cause.code)) {
    const position = cause.sourcePosition;
    const location = position?.row ? `Row ${position.row}${position.column ? `, column ${position.column}` : ""}: ` : "";
    return location + sourceMessages[cause.code];
  }
  return confirming ? "The import was rejected. Reload the records and prepare a new preview; the file, mappings or reserved numbers may have changed."
    : "The source or mapping could not be checked. Verify the selected format, delimiter or sheet, file limits and literal values, then retry.";
}

export function parseImportInspection(value: unknown) {
  const data = record(value); const sourceFormat = format(data.format);
  const sheets = unique(list(data.sheets, 16, (item) => text(item, 64)));
  if (boolean(data.requires_sheet)) {
    if (sourceFormat !== "XLSX" || sheets.length === 0) throw new Error("Invalid sheet choice");
    return { format: sourceFormat, sheets, requiresSheet: true as const };
  }
  const headers = unique(list(data.headers, 32, (item) => text(item)));
  if (!headers.length || headers.some((header) => !header.trim())) throw new Error("Invalid import headers");
  const rowCount = integer(data.row_count, 1, 2000);
  const sample = list(data.sample, Math.min(10, rowCount), (value) => {
    const row = record(value); const values = list(row.values, 32, (item) => text(item));
    if (values.length !== headers.length) throw new Error("Inconsistent import sample");
    return { row: integer(row.row, 1, 4194304), values };
  });
  let mappingValues: Record<ImportGroup, string[]> | undefined;
  if (data.mapping_values !== undefined) {
    const groups = record(data.mapping_values);
    mappingValues = { project: unique(list(groups.project, 64, (item) => text(item))),
      brand: unique(list(groups.brand, 64, (item) => text(item))), processor: unique(list(groups.processor, 64, (item) => text(item))) };
  }
  return { format: sourceFormat, sheets, requiresSheet: false as const, hash: hash(data.source_sha256), headers, rowCount, sample, mappingValues };
}
function row(value: unknown) {
  const data = record(value);
  return { sourceRow: integer(data.source_row, 1, 4194304), identity: text(data.technical_identity, 512), name: text(data.material_name, 255),
    number: integer(data.sequence_number, 1, 9999), category: text(data.main_category_code, 100), projectId: uuid(data.project_id),
    brandId: uuid(data.published_brand_id), processorId: uuid(data.assigned_processor_id) };
}
function initialState(value: unknown) {
  const data = record(value);
  if (data.workflow_status !== "IN_PROGRESS" || data.validation_status !== "NOT_CHECKED" || data.publication_status !== "NOT_PUBLISHED" ||
      data.is_published !== false || data.folder_path !== null) throw new Error("Unsafe import initial state");
}
function referenceContext(value: unknown) {
  const data = record(value);
  const projects = list(data.projects, 64, (value) => { const item = record(value);
    return { id: uuid(item.id), name: text(item.name, 255), companyId: uuid(item.company_id), number: text(item.project_number, 100) }; });
  const brands = list(data.brands, 64, (value) => { const item = record(value);
    return { id: uuid(item.id), name: text(item.name, 255), companyId: uuid(item.company_id), prefix: text(item.folder_prefix, 255) }; });
  const processors = list(data.processors, 64, (value) => { const item = record(value);
    return { id: uuid(item.id), name: text(item.display_name, 255) }; });
  const companies = list(data.companies, 128, (value) => { const item = record(value);
    return { id: uuid(item.id), name: text(item.name, 255) }; });
  for (const items of [projects, brands, processors, companies]) unique(items.map((item) => item.id));
  return { projects, brands, processors, companies };
}
export function parseImportPreview(value: unknown) {
  const data = record(value); const snapshot = record(data.snapshot);
  if (snapshot.schema_version !== 1) throw new Error("Unknown import snapshot");
  initialState(snapshot.initial_state);
  const rows = list(snapshot.rows, 2000, row); const findings = list(data.findings, 20000, finding);
  const rowCount = integer(data.row_count, 1, 2000); const ready = boolean(data.can_confirm);
  if (ready !== (findings.length === 0 && rows.length === rowCount)) throw new Error("Inconsistent import preview");
  if (!ready && data.preview_hash !== null) throw new Error("Unexpected import confirmation digest");
  const references = referenceContext(snapshot.references);
  unique(rows.map((row) => String(row.sourceRow)));
  unique(rows.map((row) => row.identity));
  unique(rows.map((row) => `${row.brandId}:${row.number}`));
  if (ready && rows.some((row) => !references.projects.some((item) => item.id === row.projectId) ||
      !references.brands.some((item) => item.id === row.brandId) || !references.processors.some((item) => item.id === row.processorId))) throw new Error("Unresolved import references");
  return { ready, hash: ready ? hash(data.preview_hash) : null, sourceHash: hash(snapshot.source_sha256), rowCount, rows, findings, references,
    ignoredColumns: list(snapshot.ignored_columns, 32, (item) => text(item)) };
}
function summary(value: unknown) {
  const data = record(value);
  return { id: uuid(data.id), actorId: uuid(data.actor_id), requestKey: uuid(data.idempotency_key), sourceHash: hash(data.source_sha256),
    previewHash: hash(data.preview_hash), format: format(data.source_format), rowCount: integer(data.row_count, 1, 2000),
    reason: text(data.reason, 2000), createdAt: text(data.created_at, 64) };
}
export function parseImportResult(value: unknown) {
  const result = summary(value); const data = record(value); const snapshot = record(data.snapshot);
  if (snapshot.schema_version !== 1 || hash(snapshot.source_sha256) !== result.sourceHash) throw new Error("Invalid import audit");
  initialState(snapshot.initial_state);
  const rows = list(data.rows, 2000, (value) => { initialState(value); return { ...row(value), materialId: uuid(record(value).material_id) }; });
  if (rows.length !== result.rowCount) throw new Error("Incomplete import result");
  unique(rows.map((row) => row.materialId)); unique(rows.map((row) => String(row.sourceRow)));
  return { ...result, rows, references: referenceContext(snapshot.references) };
}
export type ImportInspection = ReturnType<typeof parseImportInspection>;
export type ImportPreview = ReturnType<typeof parseImportPreview>;
export type ImportResult = ReturnType<typeof parseImportResult>;
export type ImportSummary = ReturnType<typeof summary>;
export const importClient = {
  async inspect(source: ImportSource, columns?: ImportColumns) {
    return parseImportInspection(await request("/material-imports/inspect", "POST", { source, ...(columns ? { columns } : {}) }));
  },
  async preview(body: ImportPlanRequest) { return parseImportPreview(await request("/material-imports/preview", "POST", body)); },
  async confirm(body: ImportConfirmation) {
    const result = parseImportResult(await request("/material-imports/confirm", "POST", body));
    if (result.requestKey !== body.idempotency_key || result.previewHash !== body.expected_preview_hash) throw new Error("Mismatched import result");
    return result;
  },
  async history(after?: string) {
    const data = record(await request("/material-imports?limit=20" + (after ? `&after=${uuid(after)}` : "")));
    const items = list(data.items, 20, summary); unique(items.map((item) => item.id));
    const next = data.next_after === null ? null : uuid(data.next_after);
    if (next !== null && next !== items.at(-1)?.id) throw new Error("Invalid import cursor");
    return { items, next };
  },
  async batch(id: string) {
    const result = parseImportResult(await request(`/material-imports/${uuid(id)}`));
    if (result.id !== id) throw new Error("Mismatched import batch");
    return result;
  },
};

export async function readImportFile(file: File): Promise<string> {
  if (file.size < 1 || file.size > 4 * 1024 ** 2) throw new Error("Choose a nonempty file up to 4 MiB.");
  const bytes = new Uint8Array(await new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.onabort = () => reject(new Error("The file read was interrupted."));
    reader.onload = () => reader.result instanceof ArrayBuffer ? resolve(reader.result) : reject(new Error("Invalid file result."));
    reader.readAsArrayBuffer(file);
  }));
  if (bytes.byteLength !== file.size) throw new Error("The selected file changed. Select it again.");
  let encoded = "";
  for (let offset = 0; offset < bytes.length; offset += 8192) encoded += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  return btoa(encoded);
}
