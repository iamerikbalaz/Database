# Database workspaces

The Customers pilot was accepted and extended to Customers, Orders, Materials,
Archived materials, the material gallery and both Catalog tabs. Development remains
on `codex/customer-scroll-pilot`. Backend APIs, schema and stored data are unchanged.
The pre-rollout version is commit `3f12b6d`.

## Automatic layout

`useDatabaseWorkspace` enables a bounded workspace at 900 x 700 CSS pixels or larger.
There is no Fixed workspace checkbox and the old Customers layout preference is
ignored. Narrow/short windows automatically use page layout. Existing sidebar
navigation collapses below 850 pixels; browser zoom follows the same CSS breakpoints.

The app bar, optional runtime banner, headings and compact controls participate in
one bounded flex layout. Table headers use native sticky positioning inside the
results scroller; no vertical scroll-time geometry or header transform is used in
workspace mode. Page mode retains its prior scrolling behavior.

Each table has one horizontal scrollbar above the header and a native vertical
scrollbar on the right. The top bar compensates for the vertical gutter so the final
column is reachable. Its spacer must remain 1 px high: zero-area boxes do not extend
scrollable overflow. Horizontal movement is controlled by the top bar (direct
horizontal wheel gestures over the contained table are not handled).

## Shared controls

- Square database list corners and an accessible icon-only Refresh button.
- Toolbar order: Select highlighted, selected count, Properties, contextual actions,
  Refresh. Ctrl/Shift highlighting and explicit checkbox selection are preserved.
- Search includes a decorative magnifying glass.
- Bulk Property/New value labels align; editors and review buttons are 40 px high.
  Notes scroll within the field and booleans center in the same control row.
- Customer logos render in grayscale; stored source images retain their colors.
- Date and additional-filter menus overlay results within the filter-panel width.
  In page layout, those filters are shown inline.

Materials and Orders place additional filters under More filters to keep space for
results. The gallery has its own native results scroller with a stable wrapper.
Materials identity columns stay pinned above horizontally scrolling header cells.

The publication preparation flow uses page layout. In a compact workspace, bulk
file-check progress and report details open in a dialog while the same operation
remains mounted; small windows show the details inline. Catalog creation/replacement
and help use dialogs. Unknown create outcomes block closing and retain exact retry
payloads and keys.

## State and verification

The full frontend suite passes: 1,136 tests in 83 files. Production build and lint
also pass.

Resize changes CSS classes/scroll mode without remounting table editors or gallery
previews. Tests cover preservation of filters, checked and highlighted rows,
unfinished notes, pending writes/navigation guards, gallery size, and Catalog dialog
drafts. They also cover archived materials, publication page transitions, report
presentation, scrollbar gutter calculations and the absence of vertical frame handlers.

Component tests use JSDOM and do not verify rendered dimensions or native scrollbar
dragging. The established browser-tool access restriction has not been bypassed.
Manually check each database at desktop and small sizes, open additional filters and
Properties, scroll to the last column, select rows with Ctrl/Shift, edit a note, open a
report or Catalog create dialog, and resize while it is open.

Code rollback: use pre-rollout commit `3f12b6d` and rebuild the frontend. No database
rollback or password reset is needed. The original pre-pilot baseline remains
`de08733` on `codex/autonomous-pbr-completion`.
