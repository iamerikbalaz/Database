import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthError, createAuthClient } from "./authClient";
import { createHttpTransport } from "./transport";

const sessionDto = {
  user: {
    id: "10000000-0000-4000-8000-000000000001",
    display_name: "Ada Lovelace",
    email: "ada@example.test",
    role: "ADMIN",
  },
  must_change_password: false,
  csrf_token: "csrf-from-response",
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function response(body: unknown, status = 200, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), { status, headers });
}

type FetchCall = (path: string, options?: RequestInit) => Promise<Response>;

describe("auth API client", () => {
  it("uses exact endpoints, allowlisted bodies and CSRF rules", async () => {
    const fetchMock = vi.fn<FetchCall>(async (path) => {
      if (path.endsWith("/logout")) return response({ status: "logged_out" });
      if (path.endsWith("/change-password")) {
        return response({
          status: "password_changed",
          reauthentication_required: true,
          changed_at: "2026-09-14T10:00:00Z",
        });
      }
      return response(sessionDto);
    });
    vi.stubGlobal("fetch", fetchMock);
    const transport = createHttpTransport();
    const client = createAuthClient(transport);
    transport.setCsrfToken("current-csrf");

    await client.getSession();
    const loginInput = {
      email: "ada@example.test",
      password: "login-password",
      confirmation: "must-not-be-sent",
    };
    await client.login(loginInput);
    await client.logout();
    const passwordInput = {
      currentPassword: "old-password",
      newPassword: "new-password",
      confirmPassword: "must-not-be-sent",
    };
    await client.changePassword(passwordInput);

    expect(fetchMock.mock.calls[0]).toEqual([
      "/api/auth/session",
      expect.objectContaining({ method: "GET", credentials: "include" }),
    ]);
    expect(fetchMock.mock.calls[0][1]?.headers).not.toHaveProperty("X-CSRF-Token");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/auth/login");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      method: "POST",
      credentials: "include",
      body: JSON.stringify({
        email: "ada@example.test",
        password: "login-password",
      }),
    });
    expect(fetchMock.mock.calls[1][1]?.headers).not.toHaveProperty("X-CSRF-Token");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/auth/logout");
    expect(fetchMock.mock.calls[2][1]).toMatchObject({ method: "POST", body: undefined });
    expect(fetchMock.mock.calls[2][1]?.headers).toHaveProperty(
      "X-CSRF-Token",
      "current-csrf",
    );
    expect(fetchMock.mock.calls[3][0]).toBe("/api/auth/change-password");
    expect(fetchMock.mock.calls[3][1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        current_password: "old-password",
        new_password: "new-password",
      }),
    });
    expect(fetchMock.mock.calls[3][1]?.headers).toHaveProperty(
      "X-CSRF-Token",
      "current-csrf",
    );
  });

  it("does not install the CSRF value returned by login", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(sessionDto)));
    const transport = createHttpTransport();
    const client = createAuthClient(transport);

    await client.login({ email: "ada@example.test", password: "password" });
    await expect(client.logout()).rejects.toMatchObject({
      kind: "http",
      status: 403,
    });
  });

  it("maps all auth responses explicitly", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(sessionDto)));
    const client = createAuthClient(createHttpTransport());

    await expect(client.getSession()).resolves.toEqual({
      user: {
        id: sessionDto.user.id,
        displayName: "Ada Lovelace",
        email: "ada@example.test",
        role: "ADMIN",
      },
      mustChangePassword: false,
      csrfToken: "csrf-from-response",
    });
  });

  it.each([400, 401, 403, 422, 429, 503])(
    "returns a safe typed HTTP error for status %i",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          response(
            { detail: "password=server-secret; internal path C:\\private" },
            status,
            status === 429 ? { "Retry-After": "17" } : undefined,
          ),
        ),
      );
      const transport = createHttpTransport();
      const listener = vi.fn();
      transport.subscribeUnauthorized(listener);
      const client = createAuthClient(transport);

      const error = await client
        .login({ email: "ada@example.test", password: "wrong-password" })
        .catch((cause: unknown) => cause);
      expect(error).toBeInstanceOf(AuthError);
      expect(error).toMatchObject({ kind: "http", status });
      expect(String(error)).not.toMatch(/server-secret|C:\\private|password=/u);
      expect((error as AuthError).retryAfterSeconds).toBe(status === 429 ? 17 : null);
      expect(listener).not.toHaveBeenCalled();
    },
  );

  it("distinguishes a network failure from an invalid response contract", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network included private URL"))
      .mockResolvedValueOnce(response({ ...sessionDto, csrf_token: undefined }));
    vi.stubGlobal("fetch", fetchMock);
    const client = createAuthClient(createHttpTransport());

    await expect(client.getSession()).rejects.toMatchObject({
      kind: "network",
      status: 0,
    });
    await expect(client.getSession()).rejects.toMatchObject({
      kind: "contract",
      status: 0,
    });
  });

  it("classifies malformed successful JSON as a contract error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("not-json", { status: 200 })),
    );
    const client = createAuthClient(createHttpTransport());

    await expect(client.getSession()).rejects.toMatchObject({
      kind: "contract",
      status: 0,
    });
  });
});
