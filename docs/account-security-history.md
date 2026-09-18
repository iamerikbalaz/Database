# Successful account security history

Additive migration 0022 introduces `account_security_events` for successful access
changes. Each event contains only target/actor IDs, action, per-account version,
mandatory-password-change outcome and timestamps. It never accepts a password,
credential hash, salt, cookie, token, session ID, request body or arbitrary metadata.

| Action | Actor recorded | Outcome |
| --- | --- | --- |
| `FIRST_ADMIN_PROVISIONED` | No application actor; local bootstrap | Temporary access; change required |
| `HOST_ACCESS_RECOVERED` | No application actor; local host recovery | Temporary access; change required |
| `ADMIN_ACCESS_PROVISIONED` | Authenticated administrator | First temporary access; change required |
| `ADMIN_ACCESS_RESET` | Authenticated administrator | Replacement temporary access; change required |
| `SELF_PASSWORD_CHANGED` | Target account owner | New password accepted; change no longer required |

The event commits in the same transaction as the credential and session-revocation
changes. Audit failure rolls those changes back. Host/bootstrap operations do not
invent a logged-in administrator identity. Existing current-account checks, CSRF,
password policy, reset confirmation and credential/session lock order remain in
force. An administrator cannot use the reset endpoint for their own account.

PostgreSQL validates the actor/action relationship, exact credential outcome and
timestamp, and consecutive version while holding credential then profile locks.
History UPDATE, DELETE and TRUNCATE are rejected; the ORM also rejects changes.
User references are preserved. Database superusers remain outside this guarantee.
Existing accounts have no invented past events. Logs still record sanitized
authentication events; this ledger is not a complete login/logout or failed-attempt
feed and does not introduce public unauthenticated audit writes.

## Read and inspect

ADMIN-only GET `/api/internal-users/{id}/security-history` returns `user_id`, at
most 20 `items` and `next_cursor`. An explicit `?after=<event UUID>` reads older
items. Cursors must belong to the same account. Current authorization and no-store
headers apply, including for disabled target profiles. Ordinary profile history is
separate; it does not contain credentials or these security actions.

Account management includes a collapsed **Account security history** section per
profile. It loads only when opened and clearly labels past outcomes and local host
origins. It offers paged reads, no restore/reset actions. Delayed responses are
discarded when the target or current actor changes. Reset confirmation and unknown
network outcomes retain the existing explicit password-entry flow; the ledger does
not add automatic retries or persist passwords in the browser.

## Operations and migration

Bootstrap/recovery commands remain in the existing authentication runbook, reading
passwords via the terminal prompt rather than arguments or environment. Upgrade
the application and schema together in a separately authorized deployment. Old
application versions would not record the new events; do not silently roll the
code back and claim continuous security-history coverage.

An empty 0022 ledger can downgrade to 0021. Once evidence exists, downgrade refuses
to erase it. Use a reviewed forward migration; never remove evidence to force a
rollback. No production migration or credential change occurred in development.

## Verification status

Local existing auth/access checks passed 138 cases; one CLI setup error came from
the shared Windows pytest temp directory. A fresh owned test directory resolved
it, and the complete CLI/history/schema/metadata suites passed 71 cases. The expanded
security-history suite then passed all 17 cases, including first-admin/host origins,
rollback, sessions, access, pagination and immutability.

Frontend: 41 focused tests and 798 full tests passed; lint/build and E2E TypeScript
passed. An initial component selector matched a collapsed event too; it now scopes
the assertion to the expanded event. Actual PostgreSQL passed **369 tests**, auth
**27/27**, no skips, including concurrent reset/self-service and prior-schema
upgrade/downgrade checks. Browser: **19 fresh + 19 retained passed**, with actual
provision/reset/self-service history and restart preservation. Desktop/390px
screenshots were visually checked without overflow; owned cleanup and protection
checks passed. Full Linux regression passed **1875 tests**, no skips, two dependency
warnings, in a nonroot, read-only container without network or host mounts. The
slice is ready for review; production deployment remains outside this work.
