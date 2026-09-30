# Customers: native fixed workspace pilot

Baseline: `de08733` on `codex/autonomous-pbr-completion`. The pilot lives on
`codex/customer-scroll-pilot`; backend, schema and data are unchanged. Fixed
workspace remains limited to Customers; shared visual-control changes are noted below.

Follow-up: shared database list frames and top scrollbars now have square corners.
Customers search includes a decorative magnifying glass. The native results
scrollbar has a reserved gutter and explicit track/thumb styling; scrollbar hiding
is scoped to page mode, so it cannot hide the workspace scrollbar.

The contained results area hides its own horizontal scrollbar and takes horizontal
position from the single native top bar. The bar uses intrinsic height without a
frame or spacer height. Vertical scrolling remains native. Direct horizontal wheel
gestures over the contained results are not handled; use the top bar.

Customer logos render in grayscale in lists and record cards while their stored
source files remain unchanged. The shared resource toolbar begins with Select
highlighted and Properties; Refresh is an accessible icon button at the end.
Bulk property/value labels use equal rows, with 40 px editors and review button;
notes scroll within that height and boolean controls center in the same row.

## Behavior

Customers defaults to **Fixed workspace** in viewports at least 900 × 700 CSS
pixels. Compact filters and controls sit above one native vertical results
scroller. Date filters and Properties open without expanding the fixed controls.
Table header cells use CSS `position: sticky; top: 0` in the same scroll area as
their rows. There is no scroll-time header geometry calculation or transform.

The horizontal scrollbar remains above results. It synchronizes only horizontal
position and compensates for the vertical scrollbar gutter so the last column
is reachable. The original single semantic table and its cell editors are retained.

The optional desktop test banner, app bar, controls and results participate in a
bounded flex layout. Short/narrow windows use page layout so controls remain
reachable; enlarging the window restores the preference. This is a Customers-only
pilot, not a global layout replacement.

At widths up to 1200 CSS pixels the compact filters wrap into three columns with
search spanning two. Below 900 pixels wide or 700 pixels high, page layout takes
over; below 850 pixels wide the existing mobile navigation applies. Browser zoom
reduces the available CSS viewport and triggers the same fallback. The table keeps
its readable column widths and horizontal scrolling rather than compressing cells.

## Immediate fallback

Clear **Fixed workspace** on Customers to restore the baseline page layout.
This does not reload data or remount the table: filters, selected rows and unfinished
cell edits remain. The preference is stored locally as `customers.workspace-view`.
Backend calls and bulk-write recovery are unchanged.

Code rollback: switch the owned development checkout to baseline `de08733` or the
unchanged `codex/autonomous-pbr-completion` branch and rebuild the frontend. No database
rollback or application-password reset is needed.

## Verification

54 targeted tests cover the viewport, Customers/Orders, common table selection,
Catalog and Materials. They include no vertical animation-frame handler in contained mode,
horizontal gutter reach, mode-switch preservation of unfinished edits, stored
layout preference and short-window fallback. Build and lint pass. Native visual
scroll smoothness still requires user testing; the existing browser-tool access
restriction was not bypassed.

Suggested manual check: open Customers, compare fast vertical scrolling with
Fixed workspace on/off, scroll to the rightmost column, edit a cell, select rows
with Ctrl/Shift and checkbox selection, open Dates/Properties, and resize the
window. Then decide whether to extend the layout to other databases.
