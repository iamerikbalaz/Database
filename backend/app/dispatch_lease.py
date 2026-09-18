"""Namespaced dispatch ownership without a long database transaction."""
from contextlib import contextmanager
import hashlib
from threading import Lock
from uuid import UUID
from weakref import WeakKeyDictionary

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


class DispatchLeaseError(RuntimeError):
    prefix = "DISPATCH"

    def __init__(self, suffix):
        self.code = self.prefix + "_" + suffix
        super().__init__("Dispatch ownership could not be verified.")


def _key(execution_id, namespace):
    if not isinstance(execution_id, UUID) or execution_id.version != 4:
        raise ValueError("Dispatch execution requires a version-4 UUID")
    value = hashlib.sha256(namespace + execution_id.bytes).digest()[:8]
    return int.from_bytes(value, "big", signed=True)


class DispatchLease:
    def __init__(self, error_type, connection=None, key=None, pid=None):
        self._error_type = error_type
        self._connection = connection
        self._key = key
        self._pid = pid
        self._active = True

    def require_owned(self):
        if not self._active:
            raise self._error_type("DISPATCH_LEASE_LOST")
        if self._connection is None: return
        try:
            # A disconnected SQLAlchemy Connection may reconnect on its next
            # statement. Never accept that new session as the old lock owner.
            if self._connection.closed or self._connection.invalidated:
                raise self._error_type("DISPATCH_LEASE_LOST")
            unsigned = self._key & ((1 << 64) - 1)
            owned = self._connection.scalar(text("""SELECT pg_backend_pid() = :pid AND EXISTS (
                SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid()
                AND database=(SELECT oid FROM pg_database WHERE datname=current_database())
                AND classid::bigint=:high AND objid::bigint=:low AND objsubid=1
                AND mode='ExclusiveLock' AND granted)"""),
                {"pid": self._pid, "high": unsigned >> 32, "low": unsigned & 0xffffffff})
            if owned is not True: raise self._error_type("DISPATCH_LEASE_LOST")
        except (SQLAlchemyError, DispatchLeaseError):
            self._active = False
            raise self._error_type("DISPATCH_LEASE_LOST") from None


_local_guard = Lock()
_local_owners = WeakKeyDictionary()


@contextmanager
def dispatch_lease(engine, execution_id, *, namespace, error_type, allow_test_sqlite=False):
    """One dedicated PG session per dispatch; SQLite is an explicit test aid only."""
    key = _key(execution_id, namespace)
    identity = (namespace, execution_id)
    if engine.dialect.name == "sqlite" and allow_test_sqlite is True:
        with _local_guard:
            owners = _local_owners.setdefault(engine, set())
            if identity in owners: raise error_type("DISPATCH_BUSY")
            owners.add(identity)
        lease = DispatchLease(error_type)
        try: yield lease
        finally:
            lease._active = False
            with _local_guard:
                owners.remove(identity)
                if not owners: _local_owners.pop(engine, None)
        return
    if engine.dialect.name != "postgresql":
        raise error_type("DATABASE_UNSUPPORTED")
    connection = None
    lease = None
    try:
        try:
            connection = engine.connect()
            connection.execution_options(isolation_level="AUTOCOMMIT")
            row = connection.execute(text("SELECT pg_backend_pid(), pg_try_advisory_lock(:key)"), {"key": key}).one()
            if row[1] is not True: raise error_type("DISPATCH_BUSY")
            lease = DispatchLease(error_type, connection, key, row[0])
            lease.require_owned()
        except SQLAlchemyError:
            raise error_type("DATABASE_UNAVAILABLE") from None
        yield lease
    finally:
        if lease is not None: lease._active = False
        if connection is not None:
            # Physically close this session on every exit, including an uncertain
            # acquisition. A session-level lock must never return to the pool.
            try: connection.invalidate()
            finally: connection.close()
