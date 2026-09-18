"""Saved historical ZIP decisions. Observed source policy never replaces one."""
from datetime import datetime
import json
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select

from app.auth.service import _aware
from app.db.models import MaterialInventory, MaterialPackagingPolicy, MaterialTechnicalCheck
from app.inventory_client import SourceInventory
from app.material_review import canonical_hash, material_context, read_review
from app.schemas import MaterialZipPolicy
from app.technical_client import TechnicalReport


def current_policy(session, material_id):
    return session.scalar(select(MaterialPackagingPolicy).where(MaterialPackagingPolicy.material_id == material_id)
        .order_by(MaterialPackagingPolicy.revision.desc()).limit(1))


def policy_view(decision):
    if decision is None: return None
    return {"id": str(decision.id), "material_id": str(decision.material_id), "revision": decision.revision,
        "previous_id": str(decision.previous_id) if decision.previous_id else None,
        "inventory_id": str(decision.inventory_id) if decision.inventory_id else None,
        "kind": "INITIAL" if decision.revision == 1 else "OVERRIDE", "policy": decision.policy,
        "storage_timezone": decision.storage_timezone, "actor_id": str(decision.actor_id), "reason": decision.reason,
        "evidence": decision.evidence, "evidence_hash": decision.evidence_hash,
        "created_at": _aware(decision.created_at).isoformat()}


def _blocked(code):
    raise HTTPException(409, {"code": code})


def initial_evidence(session, material, state, payload, storage_timezone):
    if (state is None or state.generation != payload.expected_generation or state.failure_code is not None
            or state.revision_hash != payload.expected_revision_hash or state.inventory_id != payload.expected_inventory_id
            or material.workflow_status != "DONE" or not material.folder_path or not state.technical_check_id):
        _blocked("PACKAGING_POLICY_REVIEW_CHANGED")
    stored = session.get(MaterialInventory, state.inventory_id)
    check = session.get(MaterialTechnicalCheck, state.technical_check_id)
    try:
        if (stored is None or check is None or stored.material_id != material.id or check.material_id != material.id
                or stored.generation != state.generation or stored.revision_hash != state.revision_hash
                or check.inventory_id != stored.id or check.generation != state.generation or check.revision_hash != state.revision_hash
                or stored.material_context != material_context(material) or canonical_hash(check.report) != check.report_hash):
            raise ValueError
        inventory = SourceInventory.model_validate_json(json.dumps(stored.source_inventory))
        report = TechnicalReport.model_validate_json(json.dumps({**check.report, "inventory": inventory.model_dump(mode="json")}))
        if (not report.can_approve or report.errors or not inventory.master_resolution or not inventory.master_last_modified_at
                or inventory.folder_name != material.technical_identity
                or canonical_hash({"source_files_hash": inventory.source_revision_hash, "material_context": material_context(material)}) != state.revision_hash):
            raise ValueError
        zone = ZoneInfo(storage_timezone)
        boundary = datetime(2026, 3, 4, tzinfo=zone)
        # ISO input can contain nanoseconds. Python truncates to microseconds;
        # this preserves strict-before at the whole-second midnight boundary.
        modified = datetime.fromisoformat(inventory.master_last_modified_at)
        if modified.tzinfo is None: raise ValueError
        policy = MaterialZipPolicy.LEGACY_BEFORE_2026_03_04 if modified < boundary else MaterialZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04
        if inventory.policy != policy: _blocked("PACKAGING_POLICY_TIMEZONE_MISMATCH")
        return policy.value, {"schema_version": 1, "inventory_id": str(stored.id),
            "generation": state.generation, "revision_hash": state.revision_hash,
            "source_revision_hash": inventory.source_revision_hash, "technical_check_id": str(check.id),
            "technical_report_hash": check.report_hash, "master_resolution": inventory.master_resolution,
            "master_last_modified_at": inventory.master_last_modified_at, "observed_policy": inventory.policy.value,
            "policy_boundary": boundary.isoformat(), "storage_timezone": storage_timezone}
    except (ValueError, TypeError, KeyError, OverflowError):
        _blocked("PACKAGING_POLICY_SOURCE_INVALID")


def override_preview(material, state, current, proposed_policy, expected_policy_id):
    if current is None: _blocked("PACKAGING_POLICY_NOT_SELECTED")
    if current.id != expected_policy_id: _blocked("PACKAGING_POLICY_CHANGED")
    if current.policy == proposed_policy: _blocked("PACKAGING_POLICY_UNCHANGED")
    view = {"material_id": str(material.id), "technical_identity": material.technical_identity,
        "current": {"id": str(current.id), "revision": current.revision, "policy": current.policy,
            "storage_timezone": current.storage_timezone}, "proposed_policy": proposed_policy,
        "review": read_review(state), "is_published": material.is_published,
        "effects": {"invalidate_current_approvals": True, "preserve_existing_artifacts": True,
            "published_update_required": material.is_published, "source_files_modified": False}}
    proof = {**view, "material_context": material_context(material), "workflow_status": material.workflow_status,
        "publication_status": material.publication_status,
        "technical_check_id": str(state.technical_check_id) if state and state.technical_check_id else None}
    return {**view, "preview_hash": canonical_hash(proof)}
