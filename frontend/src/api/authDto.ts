import { boolean, record, string, uuid } from "./dto";
import { userRoles } from "./materialDto";

export type UserRole = (typeof userRoles)[number];

export interface LoginRequestDto {
  email: string;
  password: string;
}

export interface ChangePasswordRequestDto {
  current_password: string;
  new_password: string;
}

export interface AuthUserDto {
  id: string;
  display_name: string;
  email: string;
  role: UserRole;
}

export interface AuthSessionDto {
  user: AuthUserDto;
  must_change_password: boolean;
  csrf_token: string;
}

export interface LogoutResponseDto {
  status: "logged_out";
}

export interface ChangePasswordResponseDto {
  status: "password_changed";
  reauthentication_required: true;
  changed_at: string;
}

export interface AuthUser {
  id: string;
  displayName: string;
  email: string;
  role: UserRole;
}

export interface AuthSession {
  user: AuthUser;
  mustChangePassword: boolean;
  csrfToken: string;
}

export interface LogoutResult {
  status: "logged_out";
}

export interface ChangePasswordResult {
  status: "password_changed";
  reauthenticationRequired: true;
  changedAt: string;
}

function requiredString(value: unknown, label: string): string {
  const parsed = string(value);
  if (parsed.trim().length === 0) throw new Error(`Invalid API ${label}`);
  return parsed;
}

function role(value: unknown): UserRole {
  const match = userRoles.find((candidate) => candidate === value);
  if (match === undefined) throw new Error("Invalid API user role");
  return match;
}

function timestamp(value: unknown): string {
  const parsed = requiredString(value, "timestamp");
  if (!Number.isFinite(Date.parse(parsed))) throw new Error("Invalid API timestamp");
  return parsed;
}

export function parseAuthUser(input: unknown): AuthUserDto {
  const value = record(input);
  return {
    id: uuid(value.id),
    display_name: requiredString(value.display_name, "display name"),
    email: requiredString(value.email, "email"),
    role: role(value.role),
  };
}

export function parseAuthSession(input: unknown): AuthSessionDto {
  const value = record(input);
  return {
    user: parseAuthUser(value.user),
    must_change_password: boolean(value.must_change_password),
    csrf_token: requiredString(value.csrf_token, "CSRF token"),
  };
}

export function parseLogoutResponse(input: unknown): LogoutResponseDto {
  const value = record(input);
  if (value.status !== "logged_out") throw new Error("Invalid logout response");
  return { status: "logged_out" };
}

export function parseChangePasswordResponse(
  input: unknown,
): ChangePasswordResponseDto {
  const value = record(input);
  if (value.status !== "password_changed") {
    throw new Error("Invalid password-change response");
  }
  if (value.reauthentication_required !== true) {
    throw new Error("Invalid password-change reauthentication response");
  }
  return {
    status: "password_changed",
    reauthentication_required: true,
    changed_at: timestamp(value.changed_at),
  };
}

export function authSessionFromDto(value: AuthSessionDto): AuthSession {
  return {
    user: {
      id: value.user.id,
      displayName: value.user.display_name,
      email: value.user.email,
      role: value.user.role,
    },
    mustChangePassword: value.must_change_password,
    csrfToken: value.csrf_token,
  };
}

export function logoutResultFromDto(value: LogoutResponseDto): LogoutResult {
  return { status: value.status };
}

export function changePasswordResultFromDto(
  value: ChangePasswordResponseDto,
): ChangePasswordResult {
  return {
    status: value.status,
    reauthenticationRequired: value.reauthentication_required,
    changedAt: value.changed_at,
  };
}
