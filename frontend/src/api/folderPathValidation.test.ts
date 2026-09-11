import { describe, expect, it } from "vitest";
import { validateFolderPath } from "./folderPathValidation";

describe("validateFolderPath", () => {
  it.each([
    ["empty", ""],
    ["whitespace only", "   "],
    ["Unicode whitespace only", " \t\u0085\u001f "],
    ["absolute Unix path", "/srv/materials"],
    ["Windows drive with backslashes", "C:\\materials\\item"],
    ["Windows drive with forward slashes", "C:/materials/item"],
    ["another Windows drive", "R:\\materials"],
    ["Windows drive-relative path", "C:materials/item"],
    ["rooted backslash path", "\\absolute"],
    ["UNC path", "\\\\server\\share"],
    ["Windows device path", "\\\\?\\C:\\item"],
    ["HTTP URL", "http://host/item"],
    ["HTTPS URL", "https://example.com"],
    ["file URI", "file:///secret"],
    ["other URI scheme", "s3://bucket/item"],
    ["URI without slashes", "urn:item"],
    ["null byte", "item\u0000/child"],
    ["control character", "item\t/child"],
    ["dot", "."],
    ["dot-dot", ".."],
    ["leading traversal", "../secret"],
    ["nested dot", "folder/."],
    ["nested dot-dot", "folder/.."],
    ["traversal", "folder/../secret"],
    ["backslash traversal", "folder\\..\\secret"],
    ["leading dot component", "./folder"],
    ["double separator", "folder//child"],
    ["trailing separator", "parent/"],
    ["relative backslash", "parent\\child"],
    ["colon in component", "folder:name"],
    ["over backend limit", "a".repeat(2049)],
  ])("rejects %s", (_label, value) => {
    expect(validateFolderPath(value).error).not.toBeNull();
  });

  it.each<[string, string]>([
    ["BRAND_0001_G03", "BRAND_0001_G03"],
    ["library/BRAND_0001_G03", "library/BRAND_0001_G03"],
    ["  brands/2026/Material 01  ", "brands/2026/Material 01"],
    ["\t\nfolder-name/sub_folder.2026\r", "folder-name/sub_folder.2026"],
    ["\u0085 žula/ČERNÁ_0001_G03 \u0085", "žula/ČERNÁ_0001_G03"],
    ["a".repeat(2048), "a".repeat(2048)],
  ])("accepts and normalizes the safe relative path %s", (value, expected) => {
    expect(validateFolderPath(value)).toEqual({ folderPath: expected, error: null });
  });

  it.each([
    [" /srv/materials ", "/srv/materials"],
    [" folder/../secret ", "folder/../secret"],
    [" \u0085/foo ", "/foo"],
    [" foo/\u0085 ", "foo/"],
    [` ${"a".repeat(2049)} `, "a".repeat(2049)],
  ])("validates the trimmed form of %s", (value, expected) => {
    const result = validateFolderPath(value);
    expect(result.folderPath).toBe(expected);
    expect(result.error).not.toBeNull();
  });
});
