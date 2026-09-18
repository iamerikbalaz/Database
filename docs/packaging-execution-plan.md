# Packaging execution: next integration slices

Policy decisions are now durable (migration 0016); the planner/staging/conversion/
ZIP/retention components work on actual synthetic Linux images. Recoverable worker
execution and the opt-in private HTTP service are now implemented and verified
(packaging-execution.md and packaging-service.md). Database reservation and explicit
action APIs are implemented; packaging-actions.md tracks their verification boundary.
The actual application/worker and UI integration now completes that boundary.
Proof-bound historical downloads are described in packaging-downloads.md. No
published state was changed and no GCS/Notion service was contacted.

## Worker execution before exposing application jobs (implemented)

The following worker requirements are implemented. The backend contract/client
independently validates the wire request and retained proof; application job
reservation and authorization are implemented and documented separately.

Create an execution journal separate from artifact retention. Freeze operation UUID,
canonical request hash, approved inventory/report digests, explicit saved ZIP policy,
storage timezone and derived plan hash before IO. A per-operation nonblocking lock
must fence execution, recovery and cleanup. A conflicting request never reuses an ID.

Allocate one private per-attempt workspace root and persist its device/inode before
staging begins. Existing stage and assembly directories live underneath that owned
root; its recorded lifetime allows cleanup after a process restart without guessing
ownership of arbitrary directories. Preserve any unrecorded reservation gap or
replaced root for operator review. Do not delete unknown directories to force retry.

Conversion descendants must retain the execution lease until they terminate;
a killed supervisor must not release the fence while ImageMagick still writes.
Test an actual killed supervisor and verify another executor stays blocked until
its surviving child exits. Do not rely solely on PID names or timestamps.

Persist completion only after retained artifacts verify and the owned temporary
root is removed. Recover retained READY bytes and missing final journal transitions
without rereading NAS. An incomplete operation requires an explicitly authorized
retry; source/approval checks precede any new copying/conversion. Known ambiguous
workspaces, corrupt artifacts and unknown collisions remain RECOVERY_REQUIRED.

Use a dedicated opt-in packaging runtime, explicit private roots, bounded memory/
disk/time/concurrency, no arbitrary executable or shell arguments, read-only source
mounts and a separate private service credential. Ordinary worker images remain
without ImageMagick. Internal packaging endpoints remain disabled until configured.
Document the maximum runtime and retry/status behavior.

## Database ownership and immutable execution inputs

Add migration 0017 or later only; existing migrations through 0016 are immutable.
Keep publication batch rows/CSV unchanged. A new execution snapshots its batch/item,
source inventory/report, approval references and saved policy decision ID/revision/
timezone. Freeze those inputs independently of mutable progress and attempt history.

Use real current account/session/role/CSRF checks when initiating or retrying.
Acquire domain/material locks in established order and validate current approved
inputs before reserving work. Conservative initial behavior can require an exact
fresh batch after any changed input, rather than silently replacing old snapshots.
Require an existing saved ZIP decision; the policy panel provides the explicit
initial choice, and a later convenience flow can use the same guarded service.

Reserve durable material ownership before worker IO. Every conflicting material,
identity, folder, brand and content mutation must consult that ownership. One
active execution per material is enforced by PostgreSQL. Do not hold a database
transaction open for image conversion. Record attempts and uncertain worker results
durably; a network timeout never implies cancellation or failure of disk writes.

Before accepting PACKAGED, recheck the persisted request, artifact proof and current
source/approval context. Revocation or later changes must prevent new execution or
publication. Recovery of already performed IO may record its factual outcome while
still withholding permission to publish; do not mislabel it as a newly authorized
action. Exact request replay returns original progress without automatically starting
another worker. Retry/reconcile is explicit and separately authorized.

## Application and verification

Expose bounded job/item/attempt history, current progress, actionable fixed errors,
explicit retry/reconcile and digest-checked artifact access. Keep packaging, upload,
online import confirmation and published status separate.

Tests must include actual PostgreSQL overlapping jobs/mutations/account changes;
fresh/prior migration, constraints and populated downgrade refusal; actual Linux
conversion and killed-process recovery; exact retry after a lost response; and
fresh/retained browser runs with only owned synthetic files and volumes. Never use
original/demo/restore databases, production NAS or live integrations.

Only after this path is verified: GCS job-prefixed transfer with proof-bound results,
explicit online importer confirmation, Notion adapter, and remaining audit,
soft-delete/restore and operational work. Production golden inputs and the deployed
importer contract remain external verification gaps.
