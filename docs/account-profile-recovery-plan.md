# Next caller: account-profile save recovery

The ordinary `internal-users` POST/PATCH routes already use the 0024 resource
command contract. Their separate administration forms still send keyless writes.
This is the next bounded UI slice after the shared record forms are verified.

1. Reuse a small shared ordinary-command controller for packet preparation,
   digest/key binding, actor ownership, exact retry and explicit read recovery.
   Keep field validation and layout in the individual forms. Avoid two independent
   recovery state machines with subtly different authorization/error behavior.
2. Identify a form by operation kind/action/target as well as its editor path:
   several account rows share `/accounts`. A pending create must never appear as
   a pending role update, and an update of one profile must never bind another.
3. Share the existing per-actor pending registry. Another ordinary pending command
   prevents a new account-profile write, and vice versa. Provide an explicit way
   to return to its form; do not silently clear a packet during navigation/reload
   of account data. Restore submitted display name/email/role or role/active state
   from the exact retained packet. Do not persist those values in browser storage.
4. Keep self-demotion/disable controls and least-privilege creation defaults.
   Successful recovery refreshes current account data; historical response values
   must not overwrite a subsequent administrator edit. Late completions cannot
   update a different actor's screen. Scope initial account loads to the actor.
5. Preserve the separate credential issuance/reset contract and immediate secret
   clearing. No password, credential response or session value may enter the
   ordinary receipt or pending packet. Security-history commands remain separate.
6. Verify unknown creates/updates, exact keys/payloads, actor and target isolation,
   late responses, navigation, refresh, first definite rejection versus later
   rejection, and continued profile/credential form behavior. Run the relevant
   frontend suites and actual fresh/retained browser scenarios before committing.

No additional database migration is required for this caller adoption.
