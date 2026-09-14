import { responseError } from "./errors";

const API_BASE_PATH = "/api";

export type CsrfMode = "auto" | "required" | "omit";
export type UnauthorizedMode = "notify" | "ignore";

export interface UnauthorizedEvent {
  expiredEpoch: number;
  currentEpoch: number;
}

export interface HttpRequestOptions {
  method?: string;
  body?: object;
  csrf?: CsrfMode;
  auth401?: UnauthorizedMode;
  signal?: AbortSignal;
  errorFactory?: (response: Response) => Error;
}

export class MissingCsrfTokenError extends Error {
  constructor() {
    super("A CSRF token is required for this request.");
    this.name = "MissingCsrfTokenError";
  }
}

export interface HttpTransport {
  request(path: string, options?: HttpRequestOptions): Promise<unknown>;
  setCsrfToken(value: string | null): number;
  getAuthEpoch(): number;
  subscribeUnauthorized(listener: (event: UnauthorizedEvent) => void): () => void;
}

function apiPath(path: string): string {
  const hasControlCharacter = Array.from(path).some((character) => {
    const codePoint = character.codePointAt(0)!;
    return codePoint < 32 || codePoint === 127;
  });
  if (
    !path.startsWith("/") ||
    path.startsWith("//") ||
    path.includes("\\") ||
    hasControlCharacter
  ) {
    throw new Error("Invalid API path");
  }
  return API_BASE_PATH + path;
}

function isMutation(method: string): boolean {
  return method !== "GET" && method !== "HEAD" && method !== "OPTIONS";
}

export function createHttpTransport(): HttpTransport {
  let csrfToken: string | null = null;
  let authEpoch = 0;
  const unauthorizedListeners = new Set<(event: UnauthorizedEvent) => void>();

  const setCsrfToken = (value: string | null): number => {
    csrfToken = value;
    authEpoch += 1;
    return authEpoch;
  };

  return {
    setCsrfToken,
    getAuthEpoch: () => authEpoch,
    subscribeUnauthorized(listener) {
      unauthorizedListeners.add(listener);
      return () => unauthorizedListeners.delete(listener);
    },
    async request(path, options = {}) {
      const method = (options.method ?? "GET").toUpperCase();
      const requestEpoch = authEpoch;
      const csrfMode = options.csrf ?? "auto";
      const tokenForRequest =
        isMutation(method) && csrfMode !== "omit" ? csrfToken : null;

      if (isMutation(method) && csrfMode === "required" && tokenForRequest === null) {
        throw new MissingCsrfTokenError();
      }

      const response = await fetch(apiPath(path), {
        method,
        credentials: "include",
        redirect: "error",
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        headers: {
          Accept: "application/json",
          ...(options.body === undefined
            ? {}
            : { "Content-Type": "application/json" }),
          ...(tokenForRequest === null ? {} : { "X-CSRF-Token": tokenForRequest }),
        },
        signal: options.signal,
      });

      if (!response.ok) {
        if (
          response.status === 401 &&
          (options.auth401 ?? "notify") === "notify" &&
          requestEpoch === authEpoch
        ) {
          const currentEpoch = setCsrfToken(null);
          const event = { expiredEpoch: requestEpoch, currentEpoch };
          for (const listener of unauthorizedListeners) {
            try {
              listener(event);
            } catch {
              // A UI listener must not replace the request's own API error.
            }
          }
        }

        if (options.errorFactory) throw options.errorFactory(response);
        const body: unknown = await response.json().catch(() => null);
        throw responseError(response.status, body);
      }

      return response.json();
    },
  };
}

export const httpTransport = createHttpTransport();
