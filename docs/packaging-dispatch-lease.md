# Backend dispatch ownership

The worker's durable journal and inherited Linux execution lease protect actual
files. The backend additionally needs to serialize run/retry/reconcile/close for
one execution while a request is in progress. This module supplies that primitive;
application endpoints are a subsequent integration step.

packaging_dispatch_lease checks out a dedicated PostgreSQL connection in DBAPI
AUTOCOMMIT mode and claims a nonblocking session advisory lock. Its key is a
domain-separated 64-bit hash of the execution UUID. A rare key collision only
rejects unrelated work as busy. Distinct executions normally have distinct leases.
The database connection carries no domain transaction while worker IO is running.

require_owned verifies the original PostgreSQL PID and exact granted advisory lock
in the current database. It refuses invalidated/closed connections before a
SQLAlchemy reconnect can create another session. A lost lease never becomes valid
again. On every exit, including uncertain acquisition and cancellation, the module
invalidates/closes its physical connection. It never returns a possibly locked
session to the pool. Process death releases the session lock on disconnect.

Use short, separately committed database sessions for authorization and dispatch
records before worker IO. Verify the lease immediately before dispatch and before
accepting its result. Durable ownership remains in place when a lease is lost;
worker recovery and current database state decide the next explicit action.
This lock is coordination, not proof that an old HTTP request was cancelled.

Only PostgreSQL is supported for deployment. An explicit allow_test_sqlite switch
offers a per-engine, process-local counterpart for unit tests; it is not a
cross-process SQLite locking claim. Tests cover competing independent PG engines,
an idle connection without an open transaction, exception cleanup, external session
termination, unexpected unlock and actual dispatch-process termination.

Semantics follow the [PostgreSQL advisory-lock documentation](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS),
the [pg_locks key representation](https://www.postgresql.org/docs/current/view-pg-locks.html)
and [SQLAlchemy DBAPI autocommit](https://docs.sqlalchemy.org/en/20/core/connections.html#understanding-the-dbapi-level-autocommit-isolation-level).
Verification results are recorded in autonomous-pbr-progress.md.
