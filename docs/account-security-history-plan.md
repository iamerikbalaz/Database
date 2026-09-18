# Next audit slice: successful account security changes

The ordinary resource ledger intentionally excludes credentials. Existing
`auth/accounts.py`, `auth/router.py`, `auth/cli.py` and `auth/recovery.py` commit
successful credential changes and then write sanitized operational log messages.
Those messages do not provide a transactional immutable administrator history.

Add a separate forward migration after 0021. Record only successful first-admin
bootstrap, administrator temporary-access issue/reset, host recovery and self-service
password change. Use explicit target user, optional authenticated actor, a fixed
source/action enum, ordered version, timestamp and safe outcome metadata such as
mandatory-password-change state. Never store passwords, credential hashes, salts,
cookies, tokens, session identifiers, request payloads or arbitrary JSON context.

Host/bootstrap commands have no authenticated application actor. Record that source
explicitly with a null actor; do not invent a user acting as the host administrator.
Application resets identify the actual administrator; self-service identifies the
same actor and target. Append the event in the credential/session-revocation
transaction. A failed audit must prevent a reported successful change. Keep failed
sign-ins/guesses in the existing rate-limited operational logging path; do not create
an unbounded public database-write endpoint for failed authentication attempts.

Enforce append-only behavior and target/action/source binding in PostgreSQL and ORM,
refuse destructive downgrade once evidence exists, preserve all prior rows, and use
explicit ADMIN-only paged reads. Extend account management with separate, correctly
labeled security history. Preserve current account/session revalidation, CSRF,
mandatory password change and credential-lock order. Review bootstrap's advisory
lock separately from application access locks before adding any new row lock.

Test real failed/rolled-back changes without copying credential values into output,
first-admin and host paths, role/target constraints, current-session revocation,
simultaneous reset/password change on actual PostgreSQL, empty/populated downgrade,
and fresh/retained browser reads. New security evidence must not turn a lost password
response into an automatic password retry. Reset idempotency is a separate product
contract: the browser already clears entered passwords and requires explicit action.

This document records the next bounded implementation; no security-ledger migration
or production credential change has been performed by it.
