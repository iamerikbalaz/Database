import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./errors";
import {
  createHttpTransport,
  MissingCsrfTokenError,
  type UnauthorizedEvent,
} from "./transport";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type FetchCall = (path: string, options?: RequestInit) => Promise<Response>;

describe("HTTP transport", () => {
  it("uses the fixed same-origin API path, cookies and redirect protection", async () => {
    const fetchMock = vi.fn<FetchCall>(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const transport = createHttpTransport();

    await transport.request("/companies");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/companies",
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        redirect: "error",
      }),
    );
  });

  it.each([
    "https://example.com/api",
    "//example.com/api",
    "auth\\session",
    "/auth\\session",
    "/auth/session\nX-Test: unsafe",
  ])("rejects a non-local or malformed API path before fetch: %s", async (path) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(createHttpTransport().request(path)).rejects.toThrow(
      "Invalid API path",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends CSRF only on mutations and supports an explicit login omission", async () => {
    const fetchMock = vi.fn<FetchCall>(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const transport = createHttpTransport();
    transport.setCsrfToken("current-csrf-token");

    await transport.request("/auth/session", { method: "GET" });
    await transport.request("/materials", { method: "POST", body: { name: "A" } });
    await transport.request("/auth/login", {
      method: "POST",
      csrf: "omit",
      body: { email: "user@example.test", password: "secret" },
    });

    const getHeaders = fetchMock.mock.calls[0][1]?.headers;
    const mutationHeaders = fetchMock.mock.calls[1][1]?.headers;
    const loginHeaders = fetchMock.mock.calls[2][1]?.headers;
    expect(getHeaders).not.toHaveProperty("X-CSRF-Token");
    expect(mutationHeaders).toHaveProperty("X-CSRF-Token", "current-csrf-token");
    expect(loginHeaders).not.toHaveProperty("X-CSRF-Token");
  });

  it("fails a required-CSRF request before contacting the server", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createHttpTransport().request("/auth/logout", {
        method: "POST",
        csrf: "required",
      }),
    ).rejects.toBeInstanceOf(MissingCsrfTokenError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("clears one auth generation and emits only once for concurrent 401s", async () => {
    const responses: Array<(response: Response) => void> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            responses.push(resolve);
          }),
      ),
    );
    const transport = createHttpTransport();
    transport.setCsrfToken("old-token");
    const events: UnauthorizedEvent[] = [];
    transport.subscribeUnauthorized((event) => events.push(event));

    const first = transport.request("/materials");
    const second = transport.request("/projects");
    await vi.waitFor(() => expect(responses).toHaveLength(2));
    responses[0](jsonResponse({ detail: "private backend text" }, 401));
    responses[1](jsonResponse({ detail: "private backend text" }, 401));

    await expect(first).rejects.toBeInstanceOf(ApiError);
    await expect(second).rejects.toBeInstanceOf(ApiError);
    expect(events).toEqual([{ expiredEpoch: 1, currentEpoch: 2 }]);
    await expect(
      transport.request("/auth/logout", { method: "POST", csrf: "required" }),
    ).rejects.toBeInstanceOf(MissingCsrfTokenError);
  });

  it("ignores a late 401 from an older generation and retains the new token", async () => {
    let resolveOldRequest!: (response: Response) => void;
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            resolveOldRequest = resolve;
          }),
      )
      .mockImplementationOnce(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const transport = createHttpTransport();
    transport.setCsrfToken("old-token");
    const listener = vi.fn();
    transport.subscribeUnauthorized(listener);

    const oldRequest = transport.request("/materials");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    transport.setCsrfToken("new-token");
    resolveOldRequest(jsonResponse({ detail: "expired" }, 401));

    await expect(oldRequest).rejects.toBeInstanceOf(ApiError);
    expect(listener).not.toHaveBeenCalled();
    await transport.request("/materials", { method: "POST" });
    expect(fetchMock.mock.calls[1][1]?.headers).toHaveProperty(
      "X-CSRF-Token",
      "new-token",
    );
  });

  it("increments the epoch for every explicit auth-state replacement", () => {
    const transport = createHttpTransport();
    expect(transport.getAuthEpoch()).toBe(0);
    expect(transport.setCsrfToken("token")).toBe(1);
    expect(transport.setCsrfToken(null)).toBe(2);
    expect(transport.setCsrfToken(null)).toBe(3);
  });
});
