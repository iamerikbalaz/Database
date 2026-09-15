# Application authorization

Conservative role decisions for the four existing roles (2026-09-15):

| Operation | PROCESSOR | PRODUCTION_LEAD | LEADERSHIP | ADMIN |
| --- | --- | --- | --- | --- |
| Read companies, brands, projects | yes | yes | yes | yes |
| Create/update companies, brands, projects | no | yes | no | yes |
| Read materials, metadata and history | assigned only | all | all | all |
| Create/assign materials | no | yes | no | yes |
| Edit name, check/link folder, Done | assigned only | all | no | all |
| Change project/category/assignment | no | yes | no | yes |
| Read user directory | own profile | all | all | all |
| Create/update user roles or provision/reset access | no | no | no | yes |

Every domain endpoint requires an active authenticated account with a completed
password change. Every unsafe request also requires the session CSRF token and
trusted origin proof. Login, session bootstrap, logout and password change remain
available to an account that must change its password.

The server rechecks the session, active flag, password-change flag and role in
the domain transaction. PostgreSQL account changes use an exclusive advisory
lock; ordinary domain transactions use the shared counterpart before credential,
session and material locks. Material assignment is checked again under the final
material lock after worker calls. These checks do not rely on the UI.

Domain unit tests explicitly substitute the access dependency to isolate their
existing validation/storage contracts. Separate HTTP authorization and PostgreSQL
tests use actual cookies, credentials, CSRF and transactions without overrides.

Processors cannot request preflight metadata for a different technical identity
by submitting its path under their own material ID. Assignment accepts only an
active PROCESSOR profile. Changing another user's role, email or active flag
revokes all their sessions. An administrator cannot demote or deactivate their
own profile; a second administrator must do it. This also preserves an active
administrator when concurrent administrators attempt to demote each other.

## Provisioning and recovery

1. Migrate the intended local/test database and run the existing first-admin
   command from `docs/auth-backend-foundation.md`. Password entry is interactive.
2. Sign in and change the initial password. Old sessions are revoked.
3. In Settings → Accounts, an administrator creates a profile and chooses
   Set or reset access. The UI POSTs to
   `/api/auth/accounts/{user_id}/access` with `current_password` (their own) and
   `new_password` (the recipient's temporary password), session cookie, trusted
   Origin and X-CSRF-Token. The same endpoint resets an existing account.
4. Convey the temporary password through an approved private channel. It is
   never returned, logged or stored in plaintext by the application. Recipients
   must change it on their next login; all previous sessions are revoked.

For a lost administrator password, a host operator can run
`python -m app.auth.recovery --email <existing-account-email>` inside the backend
environment. It prompts twice without echo, preserves role/active state, and
forces a password change. It cannot create accounts or activate a disabled one.
Never pass passwords in command arguments, environment files, URLs or logs.

The application database is unchanged by this authorization slice (head 0006).
Rolling back this slice would reopen previously public endpoints; do not expose
the earlier application version on a shared network.
