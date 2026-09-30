# Database workspaces

The Customers pilot was accepted and extended to Customers, Orders, Materials,
Archived materials, the material gallery and both Catalog tabs. Development remains
on `codex/customer-scroll-pilot`. Backend APIs and schema are unchanged.
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

Materials, Orders and Catalog use `ResponsiveFilters`: bounded field widths, a
ResizeObserver on the filter track and priority-based overflow. More filters appears
only when the controls do not fit. Filter inputs stay mounted, including when moved
to the overlay, so resizing preserves values and keyboard focus. In page layout all
filters wrap inline. The Color menu measures the available viewport/clipping space
when opened or resized, flips upwards when needed and limits its own scroll height.
Materials prioritize search, category, customer, order, status,
processor, Checked, Published, automatic check, then Color. Orders prioritize search,
customer, status, responsible, starting date, due date, then priority. Due date is an
inclusive upper bound on the order's actual due date.

The gallery has its own native results scroller with a stable wrapper.
Materials identity columns stay pinned above horizontally scrolling header cells.

The publication preparation flow uses page layout. In a compact workspace, bulk
file-check progress and report details open in a dialog while the same operation
remains mounted; small windows show the details inline. Catalog creation/replacement
and help use dialogs. Unknown create outcomes block closing and retain exact retry
payloads and keys.

Materials actions sit at the right of the selection toolbar: Auto-check selected
materials, Prepare selected for publication, Refresh. Only the check controls are
ported into that toolbar; the job and report stay mounted across table reloads.

Dashboard uses Checked = Correction, the current status labels and lazy thumbnails.
Customer logo is always the first data column, including with an older hidden-column
preference. Accounts default to Active with All/Inactive filters; status badges show
the saved boolean and the checkbox is labeled Allow sign-in. Hidden account rows keep
their drafts and unresolved writes remain reachable. Packaging settings span the page.

The separately requested local data corrections on 2026-09-30 fixed Petr Bartoš via
the audited profile API and deleted the inactive, unreferenced Marian Strelecky
profile after checking all foreign keys. A private local rollback copy was retained
outside version control. Other active/inactive values were verified and not inverted.

## State and verification

The full frontend suite passes: 1,149 tests in 85 files. Lint and the production build
pass. The running local server's JavaScript and CSS match the build byte for byte.
The suite includes priority overflow/focus, clipping of Color options,
account filter/draft preservation, customer logo preferences, dashboard Correction,
actual order due dates and material check/report preservation across toolbar reloads.

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
