export interface ValidationIssue {
  field: string;
  message: string;
}
export class ApiError extends Error {
  status: number;
  issues: ValidationIssue[];
  code?: string;
  sourcePosition?: { row?: number; column?: number };
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
  const error = new ApiError(status, message, issues);
  if (detail && typeof detail === "object" && "code" in detail && typeof detail.code === "string" && /^[A-Z][A-Z0-9_]{0,99}$/.test(detail.code)) {
    error.code = detail.code;
    const row = "row" in detail ? detail.row : undefined;
    const column = "column" in detail ? detail.column : undefined;
    error.sourcePosition = {
      ...(typeof row === "number" && Number.isSafeInteger(row) && row >= 1 && row <= 4194304 ? { row } : {}),
      ...(typeof column === "number" && Number.isSafeInteger(column) && column >= 1 && column <= 32 ? { column } : {}),
    };
  }
  return error;
}
