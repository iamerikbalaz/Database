import {
  authSessionFromDto,
  changePasswordResultFromDto,
  logoutResultFromDto,
  parseAuthSession,
  parseChangePasswordResponse,
  parseLogoutResponse,
  type AuthSession,
  type ChangePasswordRequestDto,
  type ChangePasswordResult,
  type LoginRequestDto,
  type LogoutResult,
} from "./authDto";
import {
  httpTransport,
  MissingCsrfTokenError,
  type HttpRequestOptions,
  type HttpTransport,
} from "./transport";

export type AuthErrorKind = "http" | "network" | "contract";

export class AuthError extends Error {
  readonly retryAfterSeconds: number | null;

  constructor(
    readonly status: number,
    readonly kind: AuthErrorKind,
    message: string,
    retryAfterSeconds: number | null = null,
  ) {
    super(message);
    this.name = "AuthError";
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export interface AuthRequestOptions {
  signal?: AbortSignal;
}

export interface LoginInput {
  email: string;
  password: string;
}

export interface ChangePasswordInput {
  currentPassword: string;
  newPassword: string;
}

export interface AuthClient {
  getSession(options?: AuthRequestOptions): Promise<AuthSession>;
  login(input: LoginInput, options?: AuthRequestOptions): Promise<AuthSession>;
  logout(options?: AuthRequestOptions): Promise<LogoutResult>;
  changePassword(
    input: ChangePasswordInput,
    options?: AuthRequestOptions,
  ): Promise<ChangePasswordResult>;
}

function retryAfterSeconds(response: Response): number | null {
  const value = response.headers.get("Retry-After");
  if (value === null || !/^\d+$/u.test(value)) return null;
  const seconds = Number(value);
  return Number.isSafeInteger(seconds) ? seconds : null;
}

function httpError(response: Response): AuthError {
  return new AuthError(
    response.status,
    "http",
    "The authentication request could not be completed.",
    response.status === 429 ? retryAfterSeconds(response) : null,
  );
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

async function authRequest<T>(
  transport: HttpTransport,
  path: string,
  options: HttpRequestOptions,
  parse: (input: unknown) => T,
): Promise<T> {
  let input: unknown;
  try {
    input = await transport.request(path, {
      ...options,
      auth401: "ignore",
      errorFactory: httpError,
    });
  } catch (error) {
    if (error instanceof AuthError) throw error;
    if (isAbortError(error)) throw error;
    if (error instanceof MissingCsrfTokenError) {
      throw new AuthError(
        403,
        "http",
        "The authentication security token is unavailable.",
      );
    }
    if (error instanceof TypeError) {
      throw new AuthError(
        0,
        "network",
        "The authentication service could not be reached.",
      );
    }
    throw new AuthError(
      0,
      "contract",
      "The authentication service returned an invalid response.",
    );
  }

  try {
    return parse(input);
  } catch {
    throw new AuthError(
      0,
      "contract",
      "The authentication service returned an invalid response.",
    );
  }
}

export function createAuthClient(transport: HttpTransport): AuthClient {
  return {
    async getSession(options) {
      const dto = await authRequest(
        transport,
        "/auth/session",
        { method: "GET", csrf: "omit", signal: options?.signal },
        parseAuthSession,
      );
      return authSessionFromDto(dto);
    },
    async login(input, options) {
      const payload: LoginRequestDto = {
        email: input.email,
        password: input.password,
      };
      const dto = await authRequest(
        transport,
        "/auth/login",
        { method: "POST", body: payload, csrf: "omit", signal: options?.signal },
        parseAuthSession,
      );
      return authSessionFromDto(dto);
    },
    async logout(options) {
      const dto = await authRequest(
        transport,
        "/auth/logout",
        { method: "POST", csrf: "required", signal: options?.signal },
        parseLogoutResponse,
      );
      return logoutResultFromDto(dto);
    },
    async changePassword(input, options) {
      const payload: ChangePasswordRequestDto = {
        current_password: input.currentPassword,
        new_password: input.newPassword,
      };
      const dto = await authRequest(
        transport,
        "/auth/change-password",
        { method: "POST", body: payload, csrf: "required", signal: options?.signal },
        parseChangePasswordResponse,
      );
      return changePasswordResultFromDto(dto);
    },
  };
}

export const authApiClient = createAuthClient(httpTransport);
