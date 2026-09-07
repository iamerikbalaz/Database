export interface ValidationIssue {
  field: string;
  message: string;
}
export class ApiError extends Error {
  status: number;
  issues: ValidationIssue[];
  constructor(status: number, message: string, issues: ValidationIssue[] = []) {
    super(message);
    this.status = status;
    this.issues = issues;
    this.name = "ApiError";
  }
}
export function responseError(status: number, body: unknown): ApiError {
  const detail =
    body && typeof body === "object" && "detail" in body
      ? body.detail
      : undefined;
  const issues: ValidationIssue[] = [];
  if (Array.isArray(detail)) {
    for (const item of detail) {
      if (
        item &&
        typeof item === "object" &&
        "loc" in item &&
        Array.isArray(item.loc) &&
        "msg" in item &&
        typeof item.msg === "string"
      ) {
        const field = item.loc.find(
          (part: unknown) => typeof part === "string" && part !== "body",
        );
        if (typeof field === "string")
          issues.push({ field, message: item.msg });
      }
    }
  }
  const message =
    status === 409
      ? "This value is already used by another record. " +
        (typeof detail === "string" ? detail : "Check the unique fields.")
      : status === 422
        ? "Please correct the highlighted fields and try again."
        : status === 404
          ? "The record or selected company no longer exists (404)."
          : "The request could not be completed (" +
            status +
            "). Please try again.";
  return new ApiError(status, message, issues);
}
