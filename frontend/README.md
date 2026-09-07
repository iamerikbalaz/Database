# Frontend API contract

The default client uses HTTP at VITE_API_BASE_URL (default /api).
The Vite development proxy preserves /api and forwards to
BACKEND_PROXY_TARGET (default http://localhost:8000). Docker supplies the
backend service address; no Compose change is required.

Mock data is only selected explicitly with VITE_USE_MOCK_API=true in the
development server, or by injecting mockApiClient into App in tests.
Production builds always select the HTTP client.

## Mapping

src/api/dto.ts defines backend read DTOs, runtime response parsers, and explicit
FromDto / ToDto functions. ToDto reconstructs a **read DTO**, including
server-owned fields; it is not a POST or PATCH payload. This shell provides
read operations only; future mutation forms need dedicated write DTOs.

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

## Verification

Run npm.cmd audit --omit=dev, npm.cmd run lint, npm.cmd test,
and npm.cmd run build from this directory. The repository-wide
scripts/test.ps1 and Docker smoke checks require Docker on the host.
