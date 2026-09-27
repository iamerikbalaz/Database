import { request } from "./client";
import { record, string } from "./dto";

export function packagingSettingsFromDto(value: unknown) {
  const data = record(value), cutoffDate = string(data.cutoff_date), storageTimezone = string(data.storage_timezone);
  if (typeof data.version !== "number" || !Number.isSafeInteger(data.version) || data.version < 0 ||
      !/^\d{4}-\d{2}-\d{2}$/.test(cutoffDate) || !Number.isFinite(Date.parse(cutoffDate)) ||
      !storageTimezone || storageTimezone.length > 100 || data.before_method !== "A" || data.on_or_after_method !== "B" ||
      data.legacy_zip_timestamp !== "2026-01-01T00:00:00") throw new Error("Invalid packaging settings");
  return { version: data.version, cutoffDate, storageTimezone };
}
export type PackagingSettings = ReturnType<typeof packagingSettingsFromDto>;
export interface PackagingSettingsUpdate {
  idempotency_key: string; expected_version: number; cutoff_date: string; storage_timezone: string;
}
export const packagingSettingsClient = {
  async current() { return packagingSettingsFromDto(await request("/settings/packaging")); },
  async save(payload: PackagingSettingsUpdate) {
    const saved = packagingSettingsFromDto(await request("/settings/packaging", "POST", payload));
    if (saved.version !== payload.expected_version + 1 || saved.cutoffDate !== payload.cutoff_date || saved.storageTimezone !== payload.storage_timezone)
      throw new Error("Settings response does not match request");
    return saved;
  },
};
