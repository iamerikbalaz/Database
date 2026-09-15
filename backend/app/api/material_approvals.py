"""Human approvals require a fresh report for the exact reviewed revision."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field, StringConstraints
from sqlalchemy import select

from app.api.material_review import (_material, _state, _request_hash, _replay, _require_generation, _record, ReviewMutation)
from app.auth.access import AccessDependency, CATALOG_MANAGERS, MATERIAL_EDITORS
from app.auth.service import database_now
from app.db.models import MaterialApproval, MaterialInventory, MaterialTechnicalCheck
from app.inventory_client import InventoryClientError
from app.material_review import canonical_hash, invalidate_review, material_context, read_review
from app.technical_client import TechnicalClient
from app.worker_client import Sha256

PUBLICATION_APPROVERS = frozenset({"ADMIN", "LEADERSHIP"})


class ApprovalRequest(ReviewMutation):
    kind: Literal["TECHNICAL", "PUBLICATION"]
    expected_revision_hash: Sha256
    technical_check_id: UUID
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)] | None = None
    warnings_acknowledged: Annotated[bool, Field(strict=True)] = False


def _approvals(session, material_id, state):
    if state is None or state.revision_hash is None: return []
    return list(session.scalars(select(MaterialApproval).where(MaterialApproval.material_id == material_id,
        MaterialApproval.generation == state.generation, MaterialApproval.revision_hash == state.revision_hash)
        .order_by(MaterialApproval.kind)))


def _view(session, material_id, state):
    check = session.get(MaterialTechnicalCheck, state.technical_check_id) if state and state.technical_check_id else None
    def timestamp(value):
        from datetime import UTC
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    return {"review": read_review(state), "validation": None if check is None else {
        "id": str(check.id), "actor_id": str(check.actor_id), "generation": check.generation,
        "revision_hash": check.revision_hash, "report_hash": check.report_hash,
        "created_at": timestamp(check.created_at), "report": check.report},
        "approvals": [{"id": str(item.id), "actor_id": str(item.actor_id), "kind": item.kind,
            "generation": item.generation, "revision_hash": item.revision_hash,
            "technical_check_id": str(item.technical_check_id),
            "technical_approval_id": str(item.technical_approval_id) if item.technical_approval_id else None,
            "note": item.note, "warnings_acknowledged": item.warnings_acknowledged,
            "created_at": timestamp(item.created_at)} for item in _approvals(session, material_id, state)]}


def _approval_requirements(session, material, state, payload):
    if material.workflow_status != "DONE": raise HTTPException(409, "Finish production before approving this material.")
    if state is None or state.revision_hash != payload.expected_revision_hash:
        raise HTTPException(409, "Material revision changed. Run technical validation again.")
    check = session.get(MaterialTechnicalCheck, payload.technical_check_id)
    if (check is None or check.material_id != material.id or check.generation != state.generation
            or check.revision_hash != state.revision_hash or state.technical_check_id != check.id):
        raise HTTPException(409, "Technical report changed. Reload before approving.")
    if not check.report["can_approve"] or check.report["errors"]:
        raise HTTPException(409, "Fix technical errors before approving.")
    if check.report["warnings"] and (not payload.warnings_acknowledged or not payload.note):
        raise HTTPException(422, "Acknowledge the warnings and explain the approval in a note.")
    approvals = _approvals(session, material.id, state)
    if any(item.kind == payload.kind for item in approvals):
        raise HTTPException(409, "This revision already has that approval.")
    technical = next((item for item in approvals if item.kind == "TECHNICAL"), None)
    if payload.kind == "PUBLICATION":
        if technical is None: raise HTTPException(409, "Technical approval is required before publication approval.")
        approved_check = session.get(MaterialTechnicalCheck, technical.technical_check_id)
        if approved_check.report_hash != check.report_hash:
            raise HTTPException(409, "Technical approval does not match this report.")
    return check.report_hash, technical.id if technical else None


def build_material_approvals_router(database, technical_client: TechnicalClient):
    router = APIRouter(prefix="/api/materials", tags=["material approvals"])

    @router.get("/{material_id}/technical-review")
    def get_technical_review(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            _material(session, material_id, access)
            return _view(session, material_id, _state(session, material_id))

    def execute(material_id, payload, access, *, approval=False):
        operation = payload.kind + "_APPROVAL" if approval else "TECHNICAL_CHECK"
        roles = (CATALOG_MANAGERS if payload.kind == "TECHNICAL" else PUBLICATION_APPROVERS) if approval else MATERIAL_EDITORS
        request_hash = _request_hash(operation, material_id, payload)
        with database.session() as session:
            actor = access.check(session, roles)
            material = _material(session, material_id, access)
            replay = _replay(session, actor.id, material_id, payload, request_hash)
            if replay is not None: return replay
            state = _state(session, material_id)
            _require_generation(state, payload.expected_generation)
            if not material.folder_path: raise HTTPException(409, "Link a material folder before technical validation.")
            if approval: _approval_requirements(session, material, state, payload)
            context = material_context(material)
            expected = (context, material.workflow_status, material.updated_at)
        report = None; failure = None
        try:
            report = technical_client.validate(context["folder_path"])
            if report.inventory.folder_name != context["technical_identity"]:
                raise InventoryClientError("INVENTORY_SOURCE_CHANGED")
        except InventoryClientError as exc:
            failure = exc.code
        with database.session() as session:
            actor = access.check(session, roles)
            material = _material(session, material_id, access, lock=True)
            replay = _replay(session, actor.id, material_id, payload, request_hash)
            if replay is not None: return replay
            state = _state(session, material_id, create=True)
            _require_generation(state, payload.expected_generation)
            if (material_context(material), material.workflow_status, material.updated_at) != expected:
                raise HTTPException(409, "Material changed while technical validation was running.")
            approved_report_hash = None; technical_approval_id = None
            if approval:
                approved_report_hash, technical_approval_id = _approval_requirements(session, material, state, payload)
            now = database_now(session)
            if failure:
                invalidate_review(session, material, actor.id, failure, record_event=False)
                state.checked_at = now
                body = {"detail": {"code": failure, "message": "Technical validation could not be verified."}, **_view(session, material_id, state)}
                return _record(session, material, state, actor.id, operation + "_FAILED", payload, request_hash,
                    body, 503 if failure == "INVENTORY_UNAVAILABLE" else 422)
            revision = canonical_hash({"source_files_hash": report.inventory.source_revision_hash, "material_context": context})
            facts = report.model_dump(mode="json", exclude={"inventory"})
            report_hash = canonical_hash(facts)
            previous_check = session.get(MaterialTechnicalCheck, state.technical_check_id) if state.technical_check_id else None
            if state.revision_hash != revision:
                invalidate_review(session, material, actor.id, "SOURCE_REVISION_CHANGED", record_event=False)
            elif previous_check and previous_check.report_hash != report_hash:
                invalidate_review(session, material, actor.id, "TECHNICAL_REPORT_CHANGED", record_event=False)
            inventory = MaterialInventory(material_id=material_id, actor_id=actor.id, generation=state.generation,
                revision_hash=revision, source_inventory=report.inventory.model_dump(mode="json"), material_context=context)
            session.add(inventory); session.flush()
            check = MaterialTechnicalCheck(material_id=material_id, actor_id=actor.id, inventory_id=inventory.id,
                generation=state.generation, revision_hash=revision, report_hash=report_hash, report=facts)
            session.add(check); session.flush()
            state.revision_hash = revision; state.inventory_id = inventory.id; state.technical_check_id = check.id
            state.checked_at = now; state.failure_code = None
            material.validation_status = "ERROR" if report.errors else "WARNING" if report.warnings else "VALID"
            code = 200; detail = None
            if approval:
                if (state.generation != payload.expected_generation or revision != payload.expected_revision_hash
                        or report_hash != approved_report_hash or not report.can_approve):
                    code = 409; detail = "Sources or technical findings changed. Review the new report before approving."
                else:
                    session.add(MaterialApproval(material_id=material_id, actor_id=actor.id, generation=state.generation,
                        revision_hash=revision, kind=payload.kind, technical_check_id=check.id,
                        technical_approval_id=technical_approval_id if payload.kind == "PUBLICATION" else None,
                        note=payload.note, warnings_acknowledged=payload.warnings_acknowledged))
                    session.flush()
            body = _view(session, material_id, state)
            if detail: body["detail"] = detail
            audit = {"technical_check_id": str(check.id), "report_hash": report_hash,
                     "errors": len(report.errors), "warnings": len(report.warnings)}
            return _record(session, material, state, actor.id, operation + ("_REJECTED" if detail else "_COMPLETED"),
                payload, request_hash, body, code, audit=audit)

    @router.post("/{material_id}/technical-review/run")
    def run_validation(material_id: UUID, payload: ReviewMutation, access: AccessDependency):
        return execute(material_id, payload, access)

    @router.post("/{material_id}/approvals")
    def approve(material_id: UUID, payload: ApprovalRequest, access: AccessDependency):
        return execute(material_id, payload, access, approval=True)

    return router
