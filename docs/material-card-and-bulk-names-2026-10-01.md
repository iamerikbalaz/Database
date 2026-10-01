# Material card controls and bulk name replacement

## Behavior

- Keep filters places its text left and checkbox right, above Clear filters.
  The collapsed Preview column is wider and its heading stays on one line.
- Edit names appears in the selected-material actions in both list and gallery.
  It reviews literal, case-insensitive string replacement in material names and
  uses uppercase normalized names. Customer, number and main category are retained.
  The review lists proposed names, blocked rows and warnings before confirmation.
- Name changes use the existing source identity operation: material folders,
  matching maps and metadata references are renamed together. Unpublished,
  linked In progress or Done materials are eligible; archived or published rows
  are blocked. Checked and Automatic file check must be repeated afterwards.
- A batch contains at most 100 explicitly selected materials. Each source plan
  and record revision is checked again before confirmation. Unknown outcomes stop
  the batch and retain the exact request key. Recorded unfinished operations are
  discoverable after reloading and continue through the existing resume endpoint.
  The final report remains open until the user closes it, then the list refreshes.
- The material card places Edit Name beside the title and Check material data at
  the right. Check progress/report is shown only after starting a check and remains
  available after the record refresh. Conflicting edits are disabled while busy.
- Preview arrows are compact and left aligned; refresh is an icon at the right.
  There is no gallery title or Close button. Preview and properties share an
  equal-height row. Folder/library panels sit side by side on wide screens and
  stack on smaller screens; their refresh icons sit next to the panel headings.
  Panel headings and material comboboxes use consistent compact styles.

## Scope

This is a frontend iteration using the existing schema 0042 and identity APIs.
It does not run a source rename on the live test data during verification.
The single-material rename dialog also retains its exact request after a lost
reply followed by a 4xx rejection, so a retry cannot abandon an unknown operation.

## Verification

- Full frontend: 97 files / 1,230 tests passed. ESLint, TypeScript and production
  build passed. Vite retains its existing advisory about the main chunk size.
- Fifteen bulk-name scenarios cover normalized/no-op names, stale records, plan
  context changes, unknown replies, unchanged idempotency keys, recovery, paginated
  operation discovery, mismatched receipts and unmount cancellation.
- Detail integration verifies action placement, preserved reports and exclusion
  of simultaneous property, folder, library and name changes. List/gallery tests
  verify that Edit names operates on the explicit selection and freezes filters.
- The running local instance served the fresh bundle `index-CxeXkV3H.js` and
  passed authenticated read-only API/asset acceptance with 51 material records.
  No source operation was applied during this iteration's verification. Fresh
  browser visual inspection was not performed; layout validation is through
  component tests and CSS review.
- Remote main remains `88a1f99d748d2a0edbb1fce509e13d18bfc03908`; work continues
  on `codex/customer-scroll-pilot`, without merging main.
