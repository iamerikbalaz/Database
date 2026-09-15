"""Explicit boundary for legacy domain-only unit/concurrency tests.

Auth/RBAC suites use app.main.create_app directly, with actual credentials.
There is no corresponding switch or bypass in application code.
"""
from uuid import UUID

from app.auth.access import ApplicationAccess, require_application_access
from app.db.models import InternalUser
from app.main import create_app


class DomainTestAccess(ApplicationAccess):
    def __init__(self):
        self.user = InternalUser(id=UUID(int=1), role="ADMIN", is_active=True)

    def check(self, session, roles=None, *, exclusive=False):
        return self.user


def create_domain_app(*args, **kwargs):
    application = create_app(*args, **kwargs)
    application.dependency_overrides[require_application_access] = DomainTestAccess
    return application
