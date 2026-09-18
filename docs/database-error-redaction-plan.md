# Database exception boundary

A synthetic SQLite constraint probe confirmed that default database exceptions
included bound values. A real isolated PostgreSQL failure also confirmed that
server DETAIL can retain those values even when SQLAlchemy hides parameters.
Only owned test data was used; no production database or credential was inspected.

## Implemented behavior

Application and migration engines set `hide_parameters=True`. An HTTP boundary
handles otherwise unhandled SQLAlchemy exceptions (including nested exception
groups) before framework logging can format them. It returns fixed 503
`DATABASE_UNAVAILABLE`, `Cache-Control: no-store` and `X-Content-Type-Options:
nosniff`. The configured CORS policy also applies to these responses.

The sole diagnostic is `database_request_failed` with a boolean `response_started`.
It excludes exception text, traceback, SQL, binds, server DETAIL, connection URL,
request URL/body and account data. If streaming has already started, it terminates
the response with a fixed exception whose original context is suppressed. It never
sends a second status line or appends a success terminator. Accepted-artifact
authorization and upstream stream cleanup still run normally.

Handled domain 401/403/409/422 responses retain their existing contracts. Unrelated
application exceptions remain unrelated failures. A generic 503 is an unknown
write outcome, not proof of rollback; no operation is retried by this boundary.
Existing exact-command recovery remains available where that operation provides it.
Passwords and ordinary CRUD are never automatically retried.

Alembic retains existing application loggers when configuring migration output.
Parameter hiding alone does **not** sanitize server DETAIL. This HTTP protection
does not claim to sanitize direct administrative tooling or arbitrary non-database
exceptions. Production logs and migrations have not been exercised.

## Verification checkpoint

Initial local HTTP/driver/download/health checks: **38 passed, 63.90s**, two
dependency warnings. Extended nonroot, read-only Linux regression without network
or host mounts: **258 passed, 153.76s**, no skips, two dependency warnings. Image
`reawote-error-boundary-backend-17a399675c1540e8be0e9c62a3fbc83f:test`, ID
`2d381849815b74feef649ad1a78357b33b10e069a2791ba559bc645b731f11be`.

Initial actual PostgreSQL run: **369 passed, one failed, 438.05s**, auth 27/27,
no skips, three warnings. The new test verified safe response/CORS and continued
database usability but found no log record: Alembic's existing logger setup disabled
the application logger. Its configuration now preserves existing loggers. The
corrected PostgreSQL run passed **370 tests, 444.15s**, auth **27/27**, no skips,
five dependency/schema warnings. Owned project
`reawote-test-c66e079d51d54f2f88de5c2ec773c4fc`, image manifest
`291ccf6c1fbf7512a5b891533844ad3c3383414288ca24544cb93e1b1cd9cf32`.
That image also contained the initial inactive 0023 lifecycle schema/guards; it did
not exercise lifecycle commands. Both runs removed only their owned containers and
networks and retained their own volumes. No existing migration was edited.
