# Sources for internal GCS staging

`backend/app/staging_sources.py` connects a server-revalidated reservation to the
retained-artifact reader. It performs no cloud writes, live NAS reads, source
mutation or publication transition. The [application runner](gcs-upload-jobs.md)
uses it for explicit guarded upload commands.

Construct `StagingSources` from `reserved_staging` output and the configured private
packaging client, with an async current-authorization/ownership guard. The
runner acquires its staging lease, commits immutable per-object transfer intent,
finishes its database transaction and then opens the source. The guard must also be
passed to `GcsClient.upload`; source access alone does not authorize cloud IO.

Construction independently validates the entire staging plan, exact CSV bytes/hash,
every package request/report/proof, material/execution/batch bindings, and complete
retained-file coverage. It copies mutable proof/report inputs before use. Selection
must match a planned object exactly; arbitrary paths, changed jobs/proofs/sizes and
missing or extra packages are rejected before any worker request.

`async with sources.open(spec) as blocks` yields at most 64 KiB per block. CSV comes
from the frozen batch. Package files come exclusively from the proof-bound private
artifact endpoint. The adapter verifies the worker's selected file/proof and the
stream's exact length and SHA-256, withholding the final block until source EOF and
verification. It never buffers a complete ZIP. The existing worker download deadline
and GCS transport deadline still apply; the runner must add its whole-job deadline.

Authorization is checked before and after opening, during bounded progress and
before the final block. The enclosing worker connection and suspended iterators are
closed through a shielded async exit stack, including consumer failure/cancellation.
As with any async producer, a custom iterator interrupted inside its own body must
protect its own asynchronous cleanup; the real packaging client's physical resources
belong to the separately shielded worker context. Private worker exceptions become
fixed source errors, while consumer failures retain their original type.

Offline tests cover exact CSV/file bytes, detached evidence, forged selections,
truncation/corruption/oversize, failures while opening/reading, authorization changes,
consumer failure and active cancellation. They use only synthetic contract fixtures.
This source adapter is one boundary of the implemented runner; it does not itself record
dispatch intent, accept storage receipts, upload a completion marker or publish.
