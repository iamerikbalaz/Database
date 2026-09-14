# Frontend API contract

The production client uses fixed, relative, same-origin `/api` URLs. All
requests include cookies (`credentials: "include"`) and reject redirects.
`VITE_API_BASE_URL` is not used: credentials must not be sent to another origin.
The Vite development proxy preserves /api and forwards to
BACKEND_PROXY_TARGET (default http://localhost:8000). Docker supplies the
backend service address; no Compose change is required.

Mock data is only selected explicitly with VITE_USE_MOCK_API=true in the
development server, or by injecting mockApiClient into App in tests.
Production builds always select the HTTP client.

## Mapping

src/api/dto.ts defines backend read DTOs, runtime response parsers, and explicit
FromDto / ToDto functions. ToDto reconstructs a **read DTO**, including
server-owned fields; it is not a POST or PATCH payload. Mutation forms use
dedicated write DTOs from `writeDto.ts`, `materialDto.ts` and
`materialOperationsDto.ts`.

- Company: legal_name ↔ officialName, website ↔ websiteUrl,
  vat_id ↔ vatId, notion_page_id ↔ notionPageId,
  is_active ↔ status (active / inactive).
- PublishedBrand: company_id ↔ companyId, folder_prefix ↔ folderPrefix,
  brand_identifier ↔ brandIdentifier, next_sequence_number ↔
  nextSequenceNumber, is_active ↔ isActive.
- Project: company_id ↔ companyId, project_number ↔ number,
  due_date ↔ dueDate, notes ↔ description.
  NOT_STARTED, IN_PROGRESS, DONE map to not_started, in_progress, done.
- All entities preserve id, name, nullable values, and map created_at /
  updated_at to createdAt / updatedAt.

Company detail uses GET /companies/{id}, /brands, and /projects under the
API base. The backend has no company filter, so the client filters both lists
by companyId. A failure in any request rejects the entire detail. Project
detail additionally loads its company to display the client name; project lists
resolve names using the company list.

useResource ignores stale responses after navigation, retry, or unmount.
Mobile navigation uses a native modal dialog for keyboard focus containment and
Escape. Unit tests emulate dialog methods because jsdom does not implement them;
a real browser check is still needed for browser-native focus behavior.

## Authentication

`main.tsx` mounts `AuthenticatedApp`, which always checks the real auth API,
including when resource mocks are explicitly enabled for development. The
existing `App` is the workspace component; isolated resource tests can mount it
without the production session gate. This is not a production auth bypass.

The shared `api/transport.ts` serves both the resource client and `authClient`.
Wire responses are parsed from `unknown`, allowlisted and explicitly mapped by
`authDto.ts`; auth errors never render or log backend bodies or submitted values.

| Endpoint | Request | Validated response / mapping |
| --- | --- | --- |
| POST `/api/auth/login` | `email`, `password`; no CSRF header | `user`, `must_change_password`, `csrf_token`; followed by GET session |
| GET `/api/auth/session` | No body or CSRF header | `user.id`, `display_name` → `displayName`, `email`, `role`; `must_change_password` → `mustChangePassword`; `csrf_token` → in-memory `csrfToken` |
| POST `/api/auth/logout` | No body; `X-CSRF-Token` | `{status: "logged_out"}` (200) |
| POST `/api/auth/change-password` | `current_password`, `new_password`; `X-CSRF-Token` | `status: "password_changed"`, `reauthentication_required: true`, `changed_at` → `changedAt` (200) |

Only a successful GET session installs authenticated state and its CSRF token.
Bootstrap loading/unavailable states never mount the workspace. 401 shows login;
network, 5xx or invalid response shape shows a separate unavailable state with
Retry. A successful login POST is never sufficient proof of a working cookie.
Refreshes are deduplicated; operation IDs and transport epochs ignore responses
from unmounted providers or older sessions, including concurrent resource 401s.
React StrictMode does not duplicate the initial session request.

All authenticated mutations (including existing material operations and resource
POST/PATCH) use the current in-memory CSRF token. A resource 401 clears local
session/CSRF and shows an expiry notice without a session-request loop. There is
no cookie access, Web Storage, token persistence or client-side session timer.
The server owns the HttpOnly cookie and session expiry/revocation. This follows
the [OWASP session guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
and [synchronizer CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

After login, the only redirect destinations are `/dashboard` and
`/change-password`; query return URLs are not consumed. `must_change_password`
blocks all workspace routes in the UI until password change succeeds. New
passwords are checked as 15–256 Unicode code points after NFKC, with the same
4,608-code-point raw bound as the backend. Spaces are not trimmed. Confirmation
uses NFKC equality and is never sent. The server remains authoritative for its
offline blocklist, repeated-character and account-context checks; there are no
client composition rules. Wrong current password is a safe 400 message; auth
401/403/422/429/network responses have distinct, static user-facing messages.

Successful password change clears the local session and requires a new login.
Logout uses the server endpoint; 200/401 clears state, while a network failure
explicitly warns that the server session may still be active and permits retry.
Forms lock duplicate submissions, clear passwords after server failures and
focus a safe error summary. The native account dialog supports Escape and focus
return and displays the server-provided name, email and role as plain text.

**Security boundary:** this branch adds client-side UX, not access control.
Resource endpoints remain public and `must_change_password` remains informational
on the backend. Real enforcement belongs to `feature/enforce-auth-rbac`; hiding
routes or displaying roles here must not be treated as server authorization.
Production deployments still require HTTPS and the default Secure cookie; only
the documented explicit loopback development exception permits an insecure
cookie. See `../docs/auth-backend-foundation.md` and `../docs/demo-e2e.md`.

## Verification

Run npm.cmd audit --omit=dev, npm.cmd run lint, npm.cmd test,
and npm.cmd run build from this directory. The repository-wide
scripts/test.ps1 and Docker smoke checks require Docker on the host.
Auth UI tests mount the production gate with synthetic HTTP responses; the
isolated Playwright runner uses real login and the official bootstrap CLI, not
HTTP interception. See `../docs/demo-e2e.md` for its secret-safe per-run setup.
