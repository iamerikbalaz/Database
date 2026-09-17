# Optional OpenAI draft client

This is an explicitly invoked operator CLI, separate from the web API. It reads
one material's restricted context, generates an English description and individual
tags, and saves a local proposal packet. A second command submits that packet as
AI_DRAFT. Human comparison, adoption and approval remain required. Nothing starts
on application startup or when a material is opened. No additional dependency or
migration is needed beyond the backend environment and service schema 0014.

## Scope and conservative decisions

- OpenAI is one optional adapter, not a required provider or a default model.
  The operator must select an available Responses model with structured outputs
  and provide their own API key. No model access, quality, price or paid call has
  been verified in this task.
- Only material name, brand name and selected category/collection values are
  sent to the provider. No UUID, context hash, reason, current content, credits,
  path, raw metadata, account/session information or service credential is sent.
- This first adapter does not read images or source pages. Approved URLs are
  validated but omitted from the model input. Used source IDs are always empty;
  it must not claim to have researched or verified a source. Source-grounded
  generation remains a separate, unfinished integration.
- The prompt asks for concise English text using only available facts and no
  technical guarantees. Model output remains untrusted. A structurally valid
  response is not evidence that its statements are correct.
- The requested model is retained in the local packet; the provider's returned
  model label, fixed prompt version and original context digest enter proposal
  provenance. These are recorded declarations, not independently attested claims.

## Explicit operator commands

Run from `backend` in its Python 3.13 environment. Use a private local directory
outside source assets and version control, with an existing parent directory.
For example, choose a file under the ignored repository `tmp` directory. On Unix,
new packets use mode 0600; on Windows, permissions inherit the parent directory's
ACL. The packet contains proposed marketing content and identifiers, never either
credential. Keep it private and unchanged through submission/retry.

1. In the material's administrator UI, issue one-material service access and copy
   the one-time credential. Keep that issuing session active. Default lifetime is
   15 minutes; logout, session expiry or revocation ends access.
2. Explicitly generate a new packet (this command makes a paid API request):

   ```text
   python -m app.ai_generate_cli generate --origin https://your-reawote-host --material MATERIAL_UUID --packet ../tmp/proposal.json --model YOUR_MODEL_ID --reason "Prepare a draft for human review"
   ```

   Two hidden terminal prompts request the REAWOTE service credential and the
   separate OpenAI API key. There are no credential command-line arguments or
   environment-variable fallbacks. Hidden-input failure stops the command before
   falling back to visible input. The key is not an application user password.

3. Submit the saved packet with the **same original service credential**:

   ```text
   python -m app.ai_generate_cli submit --origin https://your-reawote-host --material MATERIAL_UUID --packet ../tmp/proposal.json
   ```

   This command needs only the service credential and does not call OpenAI.
   Inspect the new proposal in the material's AI workspace. Review/edit, explicitly
   adopt, and seek the ordinary human content approval. Revoke unused service
   access afterward. Submission alone does not change the saved content revision.

## Failures, retry and limits

Generation reserves its output with exclusive creation **before** requesting any
network service. Existing files, including symlinks, are never overwritten. A
failed attempt leaves a non-submittable marker (or incomplete file after a local
write failure). The provider may still have billed an interrupted request. Inspect
the failure before explicitly choosing a new output and making another request;
the client never regenerates or retries automatically.

Submission retains the exact request UUID, payload, deployment origin, material
and original credential UUID. If its response is lost, retry the unchanged packet
with the original credential. The server returns its prior immutable result.
Do not issue another credential to retry blindly: replay namespaces differ. If
access expired or was revoked, inspect human proposal history before deciding on
a new issuance. A packet is not a signature and does not prove authenticity.

`UPSTREAM_REJECTED_401` means authentication was rejected; `..._409` can mean a
stale context or replay conflict; `..._429` means rate rejection. No upstream body
or credential is printed. Invalid/refused/incomplete generation is never submitted.
A successful receipt must contain AI_DRAFT and the packet's exact context digest.

Both services require HTTPS except explicitly selected HTTP loopback REAWOTE.
The provider endpoint is fixed to `https://api.openai.com/v1/responses`. Requests
use fresh clients without environment proxies, redirects, retained cookies or
automatic retries. Reads are limited to 256 KiB, with socket timeouts and a 120s
elapsed-time check on received chunks. Packet input must be a regular bounded
file. Generation defaults to a 4096 output-token ceiling, configurable from 256 to
16384; description is capped at 5000 characters and tags at 30 items, followed by
the normal application content validators. These are ceilings, not cost estimates.

The API request uses `text.format` JSON schema with `strict: true`. The client also
validates the response locally and handles refusal/incomplete responses explicitly,
following the official [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).
It sends `store: false` to disable response storage for later API retrieval; this
is not a claim about every provider retention policy. The request and response
fields follow the [Responses reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create),
read on 2026-09-17.

## Verification and remaining work

Local generator suite: 64 passed. It includes minimal disclosure, independent
credentials, unsafe-origin refusal, context validation before provider calls,
strict output/refusal handling, bounded failures, no automatic retries, exact
packet replay, no overwrite, hidden-input safeguards and a real application-auth
roundtrip. Its only provider transport is an in-memory test fixture. An initial
roundtrip failed because the test bridge dropped Content-Type; forwarding the
actual header fixed the harness without relaxing the API contract. Earlier joint
AI/service/adoption run passed 134 tests before three final CLI/stream tests were
added. Full Linux Docker verification is recorded in the progress checkpoint.

No live provider call, source-page research, production migration or external
publication has occurred. Live model compatibility and generation quality remain
unverified. The web workspace still has no generation button. Rollback removes
only these two optional CLI modules; it does not require a schema downgrade or
delete saved immutable proposals.
