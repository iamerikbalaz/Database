"""Explicit boundary for legacy domain-only unit/concurrency tests.

Auth/RBAC suites use app.main.create_app directly, with actual credentials.
There is no corresponding switch or bypass in application code.
"""
from uuid import UUID

from app.auth.access import ApplicationAccess, CATALOG_MANAGERS, require_application_access
from app.db.models import InternalUser
from app.main import create_app


class DomainTestAccess(ApplicationAccess):
    def __init__(self):
        self.user = InternalUser(id=UUID(int=1), role="ADMIN", is_active=True)

    def check(self, session, roles=None, *, exclusive=False):
        # Catalog writes now persist the actor in their immutable audit. Keep this
        # legacy domain-only fixture explicit; real authorization tests use actual
        # sessions and never install this override. User-only fixtures stay empty.
        if roles == CATALOG_MANAGERS and session.get(InternalUser, self.user.id) is None:
            session.add(InternalUser(id=self.user.id, display_name="Synthetic domain actor",
                email="domain-actor@example.invalid", role="ADMIN", is_active=True))
            session.flush()
        return self.user


def create_domain_app(*args, **kwargs):
    application = create_app(*args, **kwargs)
    application.dependency_overrides[require_application_access] = DomainTestAccess
    return application
