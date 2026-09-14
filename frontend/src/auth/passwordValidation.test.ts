import { describe, expect, it } from "vitest";
import { validatePasswordChange } from "./passwordValidation";

describe("backend-compatible new-password validation", () => {
  it.each(["a".repeat(15), "a".repeat(64), "a".repeat(256), " ".repeat(15), "😀".repeat(15), "é".repeat(15), "Ｆ".repeat(15)])("accepts backend length boundaries, spaces and Unicode (case %#)", (password) => {
    // Repetition/blocklist/account context are deliberately left to the server.
    expect(validatePasswordChange(password, password)).toBeNull();
  });
  it.each(["", "a".repeat(14), "😀".repeat(14), "a".repeat(257), "😀".repeat(257), "a".repeat(4609), "ﬃ".repeat(86)])("rejects normalized out-of-range lengths (case %#)", (password) => {
    expect(validatePasswordChange(password, password)).not.toBeNull();
  });
  it("counts code points and applies NFKC before length and confirmation", () => {
    expect(validatePasswordChange("ﬃ".repeat(5), "ffi".repeat(5))).toBeNull();
    expect(validatePasswordChange("e\u0301".repeat(15), "é".repeat(15))).toBeNull();
    expect(validatePasswordChange("Ｆ".repeat(15), "F".repeat(15))).toBeNull();
  });
  it("does not trim passwords or normalize beyond NFKC", () => {
    expect(validatePasswordChange("  synthetic phrase  ", "synthetic phrase")).toMatch(/do not match/);
    expect(validatePasswordChange("Synthetic Phrase", "synthetic phrase")).toMatch(/do not match/);
  });
});
