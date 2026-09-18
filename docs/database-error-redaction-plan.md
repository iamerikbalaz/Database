# Confirmed exception-output gap

During account security work, a synthetic in-memory SQLite constraint failure
confirmed that `str(DBAPIError)` currently contains its bound test value. The
production `Database` factory sets `pool_pre_ping` but not `hide_parameters`, and
the application has no SQLAlchemy exception handler. An unhandled database failure
can therefore reach framework exception logging with bound values or server detail.
The probe used only an owned memory database and printed a boolean, never a real
credential, customer value or exception body. No production database was accessed.

After the current security-history slice is verified, add a separate small fix:

- Enable SQLAlchemy bound-parameter hiding on application-created engines.
- Handle otherwise unhandled SQLAlchemy errors at the HTTP boundary with a fixed
  generic 503/no-store result and fixed structured log message, excluding exception
  string/repr, parameters, URL, request body and traceback. Keep already handled
  domain conflicts/authorization/validation responses unchanged.
- Do not claim that parameter hiding sanitizes database server DETAIL text by
  itself. The HTTP handler must not log those diagnostics. Direct administrative
  tooling remains a separate operator/logging boundary.
- Test a synthetic failing driver operation and canary-bearing exception on an
  actual ASGI request; assert that no canary appears in response/log output and
  that handled domain conflicts remain unchanged. Include current no-store/security
  headers. Verify the relevant runtime behavior in Linux and PostgreSQL.
- A generic failure is an unknown write outcome, not proof of rollback. Preserve
  existing exact-command recovery and avoid automatic password/CRUD retries.

No runtime change is made by this plan. This precedes material archive implementation
because it closes a concrete output risk in the account/database path just reviewed.
