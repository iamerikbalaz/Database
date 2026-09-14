// Python len() counts Unicode code points, not JavaScript UTF-16 code units.
// Do not trim passwords or reproduce the server's evolving offline blocklist.
export function validatePasswordChange(password: string, confirmation: string): string | null {
  if (Array.from(password).length > 4608) return "The new password is too long.";
  const normalized = password.normalize("NFKC");
  const length = Array.from(normalized).length;
  if (length < 15 || length > 256) return "Use 15–256 characters after Unicode normalization for your new password.";
  if (normalized !== confirmation.normalize("NFKC")) return "The new passwords do not match.";
  return null;
}
