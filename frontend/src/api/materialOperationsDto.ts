import { boolean, nullable, parseList, record, string, uuid } from "./dto";
import { materialFromDto, parseMaterial, type Material } from "./materialDto";

export const metadataStatuses = [
  "NOT_SCANNED",
  "MISSING",
  "VALID",
  "WARNING",
  "INVALID",
] as const;
export type MetadataStatus = (typeof metadataStatuses)[number];

const zipPolicies = [
  "LEGACY_BEFORE_2026_03_04",
  "CURRENT_ON_OR_AFTER_2026_03_04",
] as const;
type ZipPolicy = (typeof zipPolicies)[number];

function choice<T extends string>(value: unknown, choices: readonly T[]): T {
  const match = choices.find((item) => item === value);
  if (match === undefined) throw new Error("Invalid API choice");
  return match;
}

function nullableUuid(value: unknown): string | null {
  return value === null ? null : uuid(value);
}

function positiveInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1)
    throw new Error(`Invalid ${label}`);
  return value;
}

function nullableDecimal(value: unknown): string | null {
  if (value === null) return null;
  const result = string(value);
  if (!/^\d+(?:\.\d{1,4})?$/.test(result) || Number(result) <= 0)
    throw new Error("Invalid API decimal");
  return result;
}

function nullableSha256(value: unknown): string | null {
  if (value === null) return null;
  const result = string(value);
  if (!/^[0-9a-f]{64}$/.test(result)) throw new Error("Invalid API SHA-256");
  return result;
}

function nullableHexColor(value: unknown): string | null {
  if (value === null) return null;
  const result = string(value);
  if (!/^#[0-9A-F]{6}$/.test(result)) throw new Error("Invalid API HEX color");
  return result;
}

function nullableMasterResolution(value: unknown): string | null {
  if (value === null) return null;
  const result = string(value);
  if (!/^[1-9]\d*K$/.test(result))
    throw new Error("Invalid API master resolution");
  return result;
}

function pathComponent(value: unknown, label: string): string {
  const result = string(value);
  if (!result || result.trim() !== result || result.includes("/") || result.includes("\\"))
    throw new Error(`Invalid API ${label}`);
  return result;
}

function nullableSourceFilename(value: unknown): string | null {
  return value === null ? null : pathComponent(value, "source filename");
}

// Findings returned by this public API should already be sanitized. Keep a
// second UI boundary so an unexpected absolute server path can never render.
function safeRelativePath(value: unknown): string | null {
  const result = nullable(value);
  if (
    result === null ||
    result.startsWith("/") ||
    result.includes("\\") ||
    result.includes(":") ||
    result.split("/").some((part) => part === "" || part === "." || part === "..")
  )
    return null;
  return result;
}

export interface MaterialFindingDto {
  code: string;
  message: string;
  path: string | null;
}

export interface MaterialIdentityMismatchDto {
  code: "TECHNICAL_IDENTITY_MISMATCH";
  message: string;
  expected_technical_identity: string;
  actual_folder_name: string;
}

export type MaterialFinding = {
  code: string;
  message: string;
  path: string | null;
  expectedTechnicalIdentity?: string;
  actualFolderName?: string;
};

function parseFinding(input: unknown): MaterialFindingDto {
  const value = record(input);
  return {
    code: string(value.code),
    message: string(value.message),
    path: safeRelativePath(value.path),
  };
}

function parsePreflightError(input: unknown): MaterialFinding {
  const value = record(input);
  if (value.code === "TECHNICAL_IDENTITY_MISMATCH") {
    return {
      code: "TECHNICAL_IDENTITY_MISMATCH",
      message: string(value.message),
      path: null,
      expectedTechnicalIdentity: string(value.expected_technical_identity),
      actualFolderName: string(value.actual_folder_name),
    };
  }
  return findingFromDto(parseFinding(value));
}

function findingFromDto(value: MaterialFindingDto): MaterialFinding {
  return { code: value.code, message: value.message, path: value.path };
}

interface MetadataFieldsDto {
  status: MetadataStatus;
  source_filename: string | null;
  source_sha256: string | null;
  hex_color: string | null;
  width_cm: string | null;
  height_cm: string | null;
  master_resolution: string | null;
  warnings: MaterialFindingDto[];
  loaded_at: string | null;
}

export interface MaterialMetadataDto extends MetadataFieldsDto {
  material_id: string;
  current_snapshot_id: string | null;
  updated_at: string;
}

export interface MaterialMetadataSnapshotDto extends MetadataFieldsDto {
  id: string;
  material_id: string;
  sequence_number: number;
  created_at: string;
}

function parseMetadataFields(input: unknown): MetadataFieldsDto {
  const value = record(input);
  return {
    status: choice(value.status, metadataStatuses),
    source_filename: nullableSourceFilename(value.source_filename),
    source_sha256: nullableSha256(value.source_sha256),
    hex_color: nullableHexColor(value.hex_color),
    width_cm: nullableDecimal(value.width_cm),
    height_cm: nullableDecimal(value.height_cm),
    master_resolution: nullableMasterResolution(value.master_resolution),
    warnings: parseList(value.warnings, parseFinding),
    loaded_at: nullable(value.loaded_at),
  };
}

export function parseMaterialMetadata(input: unknown): MaterialMetadataDto {
  const value = record(input);
  return {
    material_id: uuid(value.material_id),
    current_snapshot_id: nullableUuid(value.current_snapshot_id),
    ...parseMetadataFields(value),
    updated_at: string(value.updated_at),
  };
}

export function parseMaterialMetadataSnapshot(
  input: unknown,
): MaterialMetadataSnapshotDto {
  const value = record(input);
  return {
    id: uuid(value.id),
    material_id: uuid(value.material_id),
    sequence_number: positiveInteger(value.sequence_number, "snapshot sequence"),
    ...parseMetadataFields(value),
    created_at: string(value.created_at),
  };
}

export interface MaterialMetadata {
  materialId: string;
  currentSnapshotId: string | null;
  status: MetadataStatus;
  sourceFilename: string | null;
  sourceSha256: string | null;
  hexColor: string | null;
  widthCm: string | null;
  heightCm: string | null;
  masterResolution: string | null;
  warnings: MaterialFinding[];
  loadedAt: string | null;
  updatedAt: string;
}

export interface MaterialMetadataSnapshot
  extends Omit<MaterialMetadata, "currentSnapshotId" | "updatedAt"> {
  id: string;
  sequenceNumber: number;
  createdAt: string;
}

export function materialMetadataFromDto(value: MaterialMetadataDto): MaterialMetadata {
  return {
    materialId: value.material_id,
    currentSnapshotId: value.current_snapshot_id,
    status: value.status,
    sourceFilename: value.source_filename,
    sourceSha256: value.source_sha256,
    hexColor: value.hex_color,
    widthCm: value.width_cm,
    heightCm: value.height_cm,
    masterResolution: value.master_resolution,
    warnings: value.warnings.map(findingFromDto),
    loadedAt: value.loaded_at,
    updatedAt: value.updated_at,
  };
}

export function materialMetadataSnapshotFromDto(
  value: MaterialMetadataSnapshotDto,
): MaterialMetadataSnapshot {
  return {
    id: value.id,
    materialId: value.material_id,
    sequenceNumber: value.sequence_number,
    status: value.status,
    sourceFilename: value.source_filename,
    sourceSha256: value.source_sha256,
    hexColor: value.hex_color,
    widthCm: value.width_cm,
    heightCm: value.height_cm,
    masterResolution: value.master_resolution,
    warnings: value.warnings.map(findingFromDto),
    loadedAt: value.loaded_at,
    createdAt: value.created_at,
  };
}

export interface MaterialFolderPreflightDto {
  schema_version: 1;
  folder_name: string;
  master_resolution: string | null;
  policy: ZipPolicy | null;
  metadata_status: MetadataStatus;
  source_filename: string | null;
  sha256: string | null;
  hex_color: string | null;
  width_cm: string | null;
  height_cm: string | null;
  warnings: MaterialFindingDto[];
  errors: Array<MaterialFindingDto | MaterialIdentityMismatchDto>;
  identity_matches: boolean;
  can_continue: boolean;
}

export interface MaterialFolderPreflight {
  schemaVersion: 1;
  folderName: string;
  masterResolution: string | null;
  policy: ZipPolicy | null;
  metadataStatus: MetadataStatus;
  sourceFilename: string | null;
  sha256: string | null;
  hexColor: string | null;
  widthCm: string | null;
  heightCm: string | null;
  warnings: MaterialFinding[];
  errors: MaterialFinding[];
  identityMatches: boolean;
  canContinue: boolean;
}

export function parseMaterialFolderPreflight(
  input: unknown,
): MaterialFolderPreflightDto {
  const value = record(input);
  if (value.schema_version !== 1) throw new Error("Invalid preflight schema version");
  return {
    schema_version: 1,
    folder_name: pathComponent(value.folder_name, "folder name"),
    master_resolution: nullableMasterResolution(value.master_resolution),
    policy: value.policy === null ? null : choice(value.policy, zipPolicies),
    metadata_status: choice(value.metadata_status, metadataStatuses),
    source_filename: nullableSourceFilename(value.source_filename),
    sha256: nullableSha256(value.sha256),
    hex_color: nullableHexColor(value.hex_color),
    width_cm: nullableDecimal(value.width_cm),
    height_cm: nullableDecimal(value.height_cm),
    warnings: parseList(value.warnings, parseFinding),
    errors: parseList(value.errors, (item) => {
      const parsed = parsePreflightError(item);
      return parsed.code === "TECHNICAL_IDENTITY_MISMATCH"
        ? {
            code: "TECHNICAL_IDENTITY_MISMATCH" as const,
            message: parsed.message,
            expected_technical_identity: parsed.expectedTechnicalIdentity!,
            actual_folder_name: parsed.actualFolderName!,
          }
        : { code: parsed.code, message: parsed.message, path: parsed.path };
    }),
    identity_matches: boolean(value.identity_matches),
    can_continue: boolean(value.can_continue),
  };
}

export function materialFolderPreflightFromDto(
  value: MaterialFolderPreflightDto,
): MaterialFolderPreflight {
  return {
    schemaVersion: value.schema_version,
    folderName: value.folder_name,
    masterResolution: value.master_resolution,
    policy: value.policy,
    metadataStatus: value.metadata_status,
    sourceFilename: value.source_filename,
    sha256: value.sha256,
    hexColor: value.hex_color,
    widthCm: value.width_cm,
    heightCm: value.height_cm,
    warnings: value.warnings.map(findingFromDto),
    errors: value.errors.map(parsePreflightError),
    identityMatches: value.identity_matches,
    canContinue: value.can_continue,
  };
}

export interface MaterialFolderRequestDto {
  folder_path: string;
}

export interface MaterialFolderLinkDto {
  material: ReturnType<typeof parseMaterial>;
  preflight: MaterialFolderPreflightDto;
}

export interface MaterialFolderLinkResult {
  material: Material;
  preflight: MaterialFolderPreflight;
}

export function parseMaterialFolderLink(input: unknown): MaterialFolderLinkDto {
  const value = record(input);
  return {
    material: parseMaterial(value.material),
    preflight: parseMaterialFolderPreflight(value.preflight),
  };
}

export function materialFolderLinkFromDto(
  value: MaterialFolderLinkDto,
): MaterialFolderLinkResult {
  return {
    material: materialFromDto(value.material),
    preflight: materialFolderPreflightFromDto(value.preflight),
  };
}

export interface MaterialMarkDoneDto {
  material: ReturnType<typeof parseMaterial>;
  metadata: MaterialMetadataDto;
  snapshot: MaterialMetadataSnapshotDto;
  preflight: MaterialFolderPreflightDto;
}

export interface MaterialMarkDoneResult extends MaterialFolderLinkResult {
  metadata: MaterialMetadata;
  snapshot: MaterialMetadataSnapshot;
}

export function parseMaterialMarkDone(input: unknown): MaterialMarkDoneDto {
  const value = record(input);
  return {
    material: parseMaterial(value.material),
    metadata: parseMaterialMetadata(value.metadata),
    snapshot: parseMaterialMetadataSnapshot(value.snapshot),
    preflight: parseMaterialFolderPreflight(value.preflight),
  };
}

export function materialMarkDoneFromDto(
  value: MaterialMarkDoneDto,
): MaterialMarkDoneResult {
  return {
    material: materialFromDto(value.material),
    metadata: materialMetadataFromDto(value.metadata),
    snapshot: materialMetadataSnapshotFromDto(value.snapshot),
    preflight: materialFolderPreflightFromDto(value.preflight),
  };
}
