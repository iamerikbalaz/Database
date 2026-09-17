"""One-material service authentication, independent of browser session transport."""
import hashlib
import hmac
import re
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from app.auth.access import ApplicationAccess, MATERIAL_EDITORS
from app.auth.service import AuthContext, _aware, database_now
from app.db.models import AiServiceCredential, AuthSession, InternalUser


def denied():
    raise HTTPException(401, "Invalid AI service credential.")


class AiServiceAccess:
    def __init__(self, credential_id, digest):
        self.credential_id = credential_id
        self.digest = digest
        self.credential = None
        self.human_access = None

    def check(self, session, roles=MATERIAL_EDITORS):
        # This first read locates the issuer. Scope fields are immutable; all
        # authorization is repeated after the normal account/session locks.
        item = session.get(AiServiceCredential, self.credential_id)
        if item is None or not hmac.compare_digest(item.token_hash, self.digest): denied()
        user = session.get(InternalUser, item.actor_id)
        stored = session.get(AuthSession, item.issuer_session_id)
        if user is None or stored is None or stored.user_id != item.actor_id: denied()
        access = ApplicationAccess(AuthContext(user, stored))
        try: actor = access.check(session, MATERIAL_EDITORS & roles)
        except HTTPException: denied()
        item = session.scalar(select(AiServiceCredential).where(AiServiceCredential.id == self.credential_id)
            .with_for_update().execution_options(populate_existing=True))
        now = database_now(session)
        if (item is None or item.revoked_at is not None or now >= _aware(item.expires_at)
                or not hmac.compare_digest(item.token_hash, self.digest)):
            denied()
        self.credential = item; self.human_access = access
        return actor

    def require_material(self, material):
        if self.credential is None or material.id != self.credential.material_id: denied()
        try: self.human_access.require_material(material)
        except HTTPException: denied()


def require_ai_service_access(request: Request):
    # No browser origin/cookie fallback and no secret in query parameters. A
    # malicious page must not convert a human session into a service capability.
    headers = request.headers.getlist("authorization")
    if (len(headers) != 1 or request.headers.get("cookie") is not None
            or request.headers.get("origin") is not None or request.query_params): denied()
    match = re.fullmatch(r"Bearer reawote_ai_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.([A-Za-z0-9_-]{43})", headers[0])
    if match is None: denied()
    access = AiServiceAccess(UUID(match[1]), hashlib.sha256(match[2].encode("ascii")).hexdigest())
    with request.app.state.database.session() as session:
        access.check(session)
        # Bind the endpoint material before body validation, including bad UUIDs.
        try: target = UUID(str(request.path_params.get("material_id")))
        except ValueError: denied()
        if target != access.credential.material_id: denied()
    return access


AiServiceDependency = Annotated[AiServiceAccess, Depends(require_ai_service_access)]
