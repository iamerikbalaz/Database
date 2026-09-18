# Storage controls on Publication

ADMIN and LEADERSHIP can open **Storage uploads** on `/publication`. Server-side
authorization, CSRF, current approvals and exact-plan checks remain authoritative.
The interface works with the internal staging layout. It cannot publish materials
or certify the deployed online importer's format.

## Preparing a job

1. Open a saved CSV batch and finish local packaging for each material.
2. Open Storage uploads. Load package history for a material and select an accepted
   package from that exact CSV batch and item snapshot. Each request reads at most
   20 jobs; older pages are explicit. Materials are shown ten at a time.
3. Select one package for every batch item, then choose **Review storage upload**.
   The server reconstructs the plan from current approvals and immutable proofs.
4. Review the destination (including the new job UUID), file/byte counts and plan
   digest. Supply a reason and acknowledge the review, then reserve the job.
5. Review the saved reservation and explicitly choose **Start storage upload**.
   Transfer must be enabled in server configuration. Reserving does not upload.

The reservation owns every material in the batch until closed. CSV, source,
approval, policy or package changes invalidate the plan. A different package
selection clears the preview. Empty, unrelated, malformed or mismatched responses
cannot enable an operation.

## Progress and recovery

History covers all batches and remains usable when no batch is selected. Job
progress refreshes every three seconds during an active request or RUNNING state.
Action and object histories use bounded, explicit pages. Receipts show the exact
generation as a string, including values above JavaScript's safe integer range.
Late poll responses cannot replace a completed action or a newly opened job.

A network/5xx/malformed-success response keeps the exact original request in memory.
**Recover same storage request** sends its original key, payload and job binding;
it does not create a replacement upload. Switching batches, collapsing the panel
or navigating away and back inside the app preserves that request for the same
actor. No automatic replay occurs. A full browser reload discards this local
packet; locate the durable job in history and inspect its progress. The page warns
before unload while a request is pending. No credentials are stored in the packet.

After a dispatch, **Check stored files** performs read-only reconciliation. It
does not replay writes. A partial upload can require explicit closure followed by
a new reservation with a fresh namespace. STAGED_VERIFIED means the complete batch
and marker passed the last storage and current-input checks. It is a point-in-time
result; online import and publication are still separate, unfinished steps.

ADMIN or LEADERSHIP can close a reservation that never dispatched, including when
GCS is disabled. Only ADMIN can close a dispatched job, with a separate explicit
acknowledgment that remote operations may finish late and stored objects remain.
Closure releases material locks; it does not promise remote cancellation or delete
anything. All results and closure evidence remain available in history.

## Verification and boundaries

Component/contract tests cover role gating, CSRF, exact recovery across navigation,
configuration disabled, independent upload/closure acknowledgments, pagination,
binding/receipt validation and late read handling. Isolated browser tests use real
CSV, worker ZIPs and PostgreSQL with GCS disabled. They exercise a deliberately lost
committed reservation response and retained history after application restart.
See `autonomous-pbr-progress.md` for completed runs and any currently running tests.

No live GCS/Notion writes or online publication were performed. Roll back the UI
commit to remove these controls; stored jobs and immutable history remain. Backend
runtime and schema compatibility/rollback are described in `gcs-upload-jobs.md`.
