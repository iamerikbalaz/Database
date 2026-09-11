const MAX_FOLDER_PATH_CODE_POINTS = 2048;
const ECMASCRIPT_WHITESPACE = /\s/u;

export interface FolderPathValidation {
  folderPath: string;
  error: string | null;
}

function trimFolderPath(value: string): string {
  const characters = Array.from(value);
  const isTrimmedByBackend = (character: string) => {
    const codePoint = character.codePointAt(0)!;
    return ECMASCRIPT_WHITESPACE.test(character) || codePoint === 0x85 ||
      (codePoint >= 0x1c && codePoint <= 0x1f);
  };
  let start = 0;
  let end = characters.length;
  while (start < end && isTrimmedByBackend(characters[start])) start += 1;
  while (end > start && isTrimmedByBackend(characters[end - 1])) end -= 1;
  return characters.slice(start, end).join("");
}

/**
 * Validate the exact folder_path value sent to the public backend API.
 *
 * The backend trims outer whitespace before validating. Return that normalized
 * path so callers validate, display and submit the exact same string.
 */
export function validateFolderPath(value: string): FolderPathValidation {
  const folderPath = trimFolderPath(value);
  const characters = Array.from(folderPath);
  const invalid = (error: string): FolderPathValidation => ({ folderPath, error });

  if (characters.length === 0) return invalid("Enter a relative folder path.");
  if (characters.length > MAX_FOLDER_PATH_CODE_POINTS) {
    return invalid(`The folder path must be ${MAX_FOLDER_PATH_CODE_POINTS} characters or fewer.`);
  }
  if (characters.some((character) => character.codePointAt(0)! < 32)) {
    return invalid("The folder path contains a control character that is not allowed.");
  }
  if (folderPath.startsWith("/") || folderPath.startsWith("\\")) {
    return invalid("Enter a relative folder path, not an absolute or network path.");
  }
  if (folderPath.includes("\\")) {
    return invalid("Use forward slashes; Windows separators and traversal are not allowed.");
  }
  if (folderPath.includes(":")) {
    return invalid("Drive paths, URLs and other URI-style paths are not allowed.");
  }

  const parts = folderPath.split("/");
  if (parts.some((part) => part.length === 0)) {
    return invalid("The folder path must not contain repeated, leading or trailing separators.");
  }
  if (parts.some((part) => part === "." || part === "..")) {
    return invalid("The folder path must not contain “.” or “..” components.");
  }
  return { folderPath, error: null };
}
