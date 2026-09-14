import { AuthError, type AuthClient, type ChangePasswordInput, type LoginInput } from "../api/authClient";
import type { AuthSession } from "../api/authDto";
import type { HttpTransport } from "../api/transport";
import { authMessage } from "./messages";

export type SessionState =
  | { status: "loading" }
  | { status: "anonymous"; notice: string }
  | { status: "unavailable"; message: string }
  | { status: "authenticated"; session: AuthSession; pendingMutation: "logout" | "change" | null };

// No browser storage, cookie access or timers asserting that a session is valid.
// Only a fresh, validated GET /auth/session can install authenticated state.
export class SessionStore {
  private state: SessionState = { status: "loading" };
  private listeners = new Set<() => void>();
  private connected = false;
  private started = false;
  private operation = 0;
  private ownedEpoch = -1;
  private pendingRefresh: Promise<void> | null = null;
  private mutating = false;

  constructor(private client: AuthClient, private transport: HttpTransport) {}

  getSnapshot = (): SessionState => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private publish(state: SessionState) {
    this.state = state;
    this.listeners.forEach((listener) => listener());
  }
  private setToken(token: string | null) {
    this.ownedEpoch = this.transport.setCsrfToken(token);
  }
  private anonymous(notice: string) {
    this.operation++;
    this.pendingRefresh = null;
    this.setToken(null);
    this.publish({ status: "anonymous", notice });
  }
  connect = () => {
    this.connected = true;
    const unsubscribe = this.transport.subscribeUnauthorized(() => {
      this.anonymous("Your session has expired. Please sign in again.");
    });
    if (!this.started) {
      this.started = true;
      this.setToken(null);
      void this.refresh();
    }
    return () => {
      this.connected = false;
      unsubscribe();
      // React StrictMode reconnects synchronously. A real unmount invalidates
      // pending work without allowing an older root to clear a newer root's token.
      queueMicrotask(() => {
        if (this.connected) return;
        this.operation++;
        this.pendingRefresh = null;
        this.started = false;
        if (this.transport.getAuthEpoch() === this.ownedEpoch) this.setToken(null);
      });
    };
  };
  refresh = (): Promise<void> => {
    if (this.pendingRefresh) return this.pendingRefresh;
    const operation = ++this.operation;
    const epoch = this.transport.getAuthEpoch();
    this.publish({ status: "loading" });
    const current = () => this.connected && operation === this.operation && epoch === this.transport.getAuthEpoch();
    const pending = Promise.resolve().then(() => this.client.getSession()).then(
      (session) => {
        if (!current()) return;
        this.setToken(session.csrfToken);
        this.publish({ status: "authenticated", session, pendingMutation: null });
      },
      (error: unknown) => {
        if (!current()) return;
        this.setToken(null);
        if (error instanceof AuthError && error.status === 401) {
          this.publish({ status: "anonymous", notice: "" });
        } else {
          this.publish({ status: "unavailable", message: authMessage(error, "session") });
        }
      },
    ).finally(() => {
      if (this.pendingRefresh === pending) this.pendingRefresh = null;
    });
    this.pendingRefresh = pending;
    return pending;
  };
  signIn = async (input: LoginInput): Promise<void> => {
    if (this.mutating) return;
    this.mutating = true;
    const operation = ++this.operation;
    this.pendingRefresh = null;
    this.setToken(null);
    try {
      await this.client.login(input);
      if (!this.connected || operation !== this.operation) return;
      // Never install the POST result as proof of a usable cookie session.
      await this.refresh();
      if (this.connected && this.state.status === "anonymous" && this.state.notice === "") {
        this.publish({ status: "anonymous", notice: "Sign-in could not be verified. Please sign in again." });
      }
    } finally {
      this.mutating = false;
    }
  };
  private async mutateSession(action: () => Promise<unknown>, notice: string, kind: "logout" | "change"): Promise<void> {
    if (this.mutating || this.state.status !== "authenticated") return;
    this.mutating = true;
    const operation = ++this.operation;
    this.pendingRefresh = null;
    this.setToken(this.state.session.csrfToken);
    this.publish({ ...this.state, pendingMutation: kind });
    try {
      await action();
      if (this.connected && operation === this.operation) this.anonymous(notice);
    } catch (error) {
      if (this.connected && operation === this.operation) {
        if (error instanceof AuthError && error.status === 401) {
          this.anonymous("Your session has expired. Please sign in again.");
        } else {
          // In particular, a network error is NOT evidence of server revocation.
          throw error;
        }
      }
    } finally {
      this.mutating = false;
      if (this.connected && operation === this.operation && this.state.status === "authenticated") {
        this.publish({ ...this.state, pendingMutation: null });
      }
    }
  }
  signOut = () => this.mutateSession(() => this.client.logout(), "You have signed out.", "logout");
  changePassword = (input: ChangePasswordInput) => this.mutateSession(
    () => this.client.changePassword(input),
    "Your password was changed. Please sign in again with your new password.",
    "change",
  );
}
