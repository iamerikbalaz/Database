import { describe, expect, it } from "vitest";
import {
  authSessionFromDto,
  changePasswordResultFromDto,
  parseAuthSession,
  parseChangePasswordResponse,
  parseLogoutResponse,
} from "./authDto";

const sessionDto = {
  user: {
    id: "10000000-0000-4000-8000-000000000001",
    display_name: "Ada Lovelace",
    email: "ada@example.test",
    role: "PRODUCTION_LEAD",
    is_active: true,
  },
  must_change_password: false,
  csrf_token: "csrf-secret-kept-in-memory",
  unexpected: "ignored",
};

describe("auth DTO mapping", () => {
  it("validates snake_case session data and maps only public fields", () => {
    expect(authSessionFromDto(parseAuthSession(sessionDto))).toEqual({
      user: {
        id: sessionDto.user.id,
        displayName: "Ada Lovelace",
        email: "ada@example.test",
        role: "PRODUCTION_LEAD",
      },
      mustChangePassword: false,
      csrfToken: "csrf-secret-kept-in-memory",
    });
  });

  it.each([
    { user: undefined },
    { must_change_password: "false" },
    { csrf_token: " " },
    { user: { ...sessionDto.user, id: "not-a-uuid" } },
    { user: { ...sessionDto.user, display_name: "" } },
    { user: { ...sessionDto.user, email: null } },
    { user: { ...sessionDto.user, role: "OWNER" } },
  ])("rejects malformed required session data %j", (override) => {
    expect(() => parseAuthSession({ ...sessionDto, ...override })).toThrow();
  });

  it("validates fixed logout and password-change response semantics", () => {
    expect(parseLogoutResponse({ status: "logged_out", extra: true })).toEqual({
      status: "logged_out",
    });
    expect(
      changePasswordResultFromDto(
        parseChangePasswordResponse({
          status: "password_changed",
          reauthentication_required: true,
          changed_at: "2026-09-14T12:34:56Z",
        }),
      ),
    ).toEqual({
      status: "password_changed",
      reauthenticationRequired: true,
      changedAt: "2026-09-14T12:34:56Z",
    });
  });

  it.each([
    { status: "done" },
    {},
  ])("rejects an invalid logout response %j", (value) => {
    expect(() => parseLogoutResponse(value)).toThrow();
  });

  it.each([
    { status: "changed", reauthentication_required: true, changed_at: "2026-09-14T12:34:56Z" },
    { status: "password_changed", reauthentication_required: false, changed_at: "2026-09-14T12:34:56Z" },
    { status: "password_changed", reauthentication_required: true, changed_at: "not-a-date" },
  ])("rejects an invalid password-change response %j", (value) => {
    expect(() => parseChangePasswordResponse(value)).toThrow();
  });
});
