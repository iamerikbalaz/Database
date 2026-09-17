"""Common proposal transaction with distinct service authentication and replay."""
from uuid import uuid5
from fastapi import HTTPException
from app.ai_content import publishing_context, ai_draft_view
from app.api.material_review import _material, _replay, _record, _request_hash, _state
from app.auth.access import MATERIAL_EDITORS
from app.db.models import MaterialAiDraft
from app.material_identity import require_material_idle


def receive_ai_draft(database, material_id, payload, access, *, service=False):
    with database.session() as session:
        actor = access.check(session, MATERIAL_EDITORS)
        credential = access.credential if service else None
        operation = "AI_SERVICE_DRAFT_RECEIVED:" + str(credential.id) if credential else "AI_DRAFT_RECEIVED"
        request_hash = _request_hash(operation, material_id, payload)
        # Human and distinct service issuances can independently reuse a client
        # UUID. The request hash still binds the original key and service identity.
        audit_payload = payload.model_copy(update={"idempotency_key": uuid5(credential.id, str(payload.idempotency_key))}) if credential else payload
        material = _material(session, material_id, access, lock=True)
        if service: access.check(session)  # Expiry after a material-lock wait.
        replay = _replay(session, actor.id, material_id, audit_payload, request_hash)
        if replay is not None: return replay
        require_material_idle(session, material_id)
        current = publishing_context(session, material)
        if payload.expected_context_hash != current["context_hash"]:
            raise HTTPException(409, {"code": "AI_CONTEXT_CHANGED"})
        selected = [str(item) for item in payload.source_link_ids]
        if not set(selected).issubset(item["id"] for item in current["context"]["source_urls"]):
            raise HTTPException(422, {"code": "AI_SOURCE_NOT_APPROVED"})
        if service: access.check(session)  # Context may have waited for a brand lock.
        item = MaterialAiDraft(material_id=material_id, actor_id=actor.id, context_hash=current["context_hash"],
            content_revision=current["content_revision"], context=current["context"], provider=payload.provider,
            model=payload.model, prompt_version=payload.prompt_version, description=payload.description,
            tags=payload.tags, source_link_ids=selected, reason=payload.reason,
            service_credential_id=credential.id if credential else None)
        session.add(item); session.flush()
        attribution = {"service_credential_id": str(credential.id)} if credential else {}
        body = {"id": str(item.id), "status": "AI_DRAFT", "context_hash": item.context_hash} if credential else ai_draft_view(item)
        return _record(session, material, _state(session, material_id, create=True), actor.id,
            "AI_SERVICE_DRAFT_RECEIVED" if credential else "AI_DRAFT_RECEIVED", audit_payload, request_hash, body, code=201,
            audit={"draft_id": str(item.id), "context_hash": item.context_hash, "source_link_ids": selected, **attribution})
