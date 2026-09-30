"""Directory projections keep material identities separate from customer profiles."""
import re
import unicodedata
from datetime import date
from pathlib import PureWindowsPath
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.db.models import PBRMaterial, Project, PublishedBrand

CUSTOMER_FIELDS = ("id", "name", "company_id", "is_customer", "customer_status", "customer_brand_identifier", "folder_prefix",
    "website", "address", "shipping_address", "legal_name", "vat_id", "description", "notes", "is_active",
    "notion_page_id", "logo_sha256", "logo_content_type", "logo_filename")
ORDER_FIELDS = ("id", "project_number", "customer_id", "company_id", "project_type", "starting_date", "due_date", "notes",
    "responsible_id", "responsible_notion_page_ids", "order_status", "priority", "name", "folder_path", "notion_page_id")


def generated_order_name(number: str, customer_name: str, project_type: str | None, starting_date: date | None) -> str:
    """Exactly upper(join([Number,Customer,Project type,MMYYYY], '_'))."""
    return "_".join((number, customer_name, project_type or "", starting_date.strftime("%m%Y") if starting_date else "")).upper()


def folder_prefix_for_customer(name: str) -> str:
    stem = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", "-", stem).strip("-")[:200] or "CUSTOMER"


def require_available_customer_prefix(session, prefix, customer_id=None):
    """Historical prefixes remain owned by their material's Customer."""
    from fastapi import HTTPException
    from app.material_naming import match_identity
    for identifier, stored in session.execute(select(PublishedBrand.id, PublishedBrand.folder_prefix)):
        if identifier != customer_id and stored.casefold() == prefix.casefold():
            raise HTTPException(409, {"code": "CUSTOMER_PREFIX_USED", "message": "Another customer already uses this prefix."})
    for owner, identity in session.execute(select(PBRMaterial.published_brand_id, PBRMaterial.technical_identity)):
        parsed = match_identity(identity)
        if owner != customer_id and parsed and parsed["prefix"].casefold() == prefix.casefold():
            raise HTTPException(409, {"code": "CUSTOMER_PREFIX_RESERVED", "message": "Historical materials reserve this prefix for their customer."})
    from app.db.models import MaterialIdentityHistory
    for old, new in session.execute(select(MaterialIdentityHistory.old_context, MaterialIdentityHistory.new_context)):
        for context in (old, new):
            parsed = match_identity(context.get("technical_identity", ""))
            if context.get("published_brand_id") != str(customer_id) and parsed and parsed["prefix"].casefold() == prefix.casefold():
                raise HTTPException(409, {"code": "CUSTOMER_PREFIX_RESERVED", "message": "Material identity history reserves this prefix for its original customer."})


def directory_snapshot(item) -> dict:
    fields = CUSTOMER_FIELDS if isinstance(item, PublishedBrand) else ORDER_FIELDS
    return jsonable_encoder({key: getattr(item, key) for key in fields})


def customer_categories(session, customer_ids: list[UUID]) -> dict[UUID, list[str]]:
    result = {identifier: [] for identifier in customer_ids}
    if not customer_ids:
        return result
    rows = session.execute(select(PBRMaterial.published_brand_id, PBRMaterial.main_category_code)
        .where(PBRMaterial.published_brand_id.in_(customer_ids)).distinct()
        .order_by(PBRMaterial.main_category_code))
    for identifier, code in rows:
        result[identifier].append(code)
    return result


def _sync(session, kind, item):
    cached = session.info.get("directory_sync_states")
    if cached is not None:
        return cached.get((kind, item.id), {"status": "NOT_QUEUED", "error_code": None, "synced_at": None, "page_id": item.notion_page_id})
    from app.notion_outbound import sync_status
    return sync_status(session, kind, item.id)


def prefetch_directory_states(session, kind, identifiers):
    from app.db.notion_sync_models import NotionSyncState, OrderFolderOperation
    states = session.scalars(select(NotionSyncState).where(NotionSyncState.entity_type == kind, NotionSyncState.entity_id.in_(identifiers)))
    session.info["directory_sync_states"] = {(kind, state.entity_id): {
        "status": state.status, "error_code": state.error_code, "synced_at": state.synced_at,
        "page_id": state.page_id, "pending_changes": state.revision != state.synced_revision,
    } for state in states}
    if kind == "ORDER":
        operations = session.scalars(select(OrderFolderOperation).where(OrderFolderOperation.order_id.in_(identifiers))
            .order_by(OrderFolderOperation.created_at.desc(), OrderFolderOperation.id.desc()))
        by_order = {}
        for operation in operations:
            if operation.order_id not in by_order:
                by_order[operation.order_id] = {"status": operation.status, "error_code": operation.error_code,
                    "operation_id": str(operation.id), "action": operation.action, "target_name": operation.target_name}
        session.info["directory_folder_operations"] = by_order


def customer_view(session, customer, categories=None, *, sync_enabled=False):
    if categories is None:
        categories = customer_categories(session, [customer.id])[customer.id]
    return jsonable_encoder({
        "id": customer.id, "legacy_company_id": customer.company_id,
        "name": customer.name, "status": customer.customer_status,
        "brand_identifier": customer.customer_brand_identifier,
        "folder_prefix": customer.folder_prefix,
        **{key: getattr(customer, key) for key in ("website", "address", "shipping_address", "legal_name", "vat_id", "description", "notes", "is_active", "notion_page_id", "created_at", "updated_at")},
        "main_category_codes": categories,
        "has_logo": customer.logo_sha256 is not None,
        "logo_url": f"/api/customers/{customer.id}/logo?v={customer.logo_sha256}" if customer.logo_sha256 else None,
        "sync": {**_sync(session, "CUSTOMER", customer), "enabled": sync_enabled},
    })


def order_view(session, order, *, sync_enabled=False):
    from app.order_folders import folder_status
    customers = session.info.get("directory_customers")
    customer = customers.get(order.customer_id) if customers is not None else session.get(PublishedBrand, order.customer_id) if order.customer_id else None
    generated = generated_order_name(order.project_number, customer.name, order.project_type, order.starting_date) if customer and order.project_type and order.starting_date else order.name
    return jsonable_encoder({
        "id": order.id, "number": order.project_number, "customer_id": order.customer_id,
        "legacy_company_id": order.company_id,
        **{key: getattr(order, key) for key in ("project_type", "starting_date", "due_date", "notes", "responsible_id", "responsible_notion_page_ids", "priority", "folder_path", "notion_page_id", "created_at", "updated_at")},
        "status": order.order_status, "generated_name": generated,
        "folder_name_matches": bool(order.folder_path and PureWindowsPath(order.folder_path).name == generated),
        "sync": {**_sync(session, "ORDER", order), "enabled": sync_enabled},
        "folder_operation": session.info["directory_folder_operations"].get(order.id, {"status": "NOT_QUEUED", "error_code": None}) if "directory_folder_operations" in session.info else folder_status(session, order.id),
    })


def legacy_order_status(status):
    if status in {"Done", "Canceled", "Test complete", "invoiced"}:
        return "DONE"
    if status in {"Not started", "Price offer sent", "Waiting for samples"}:
        return "NOT_STARTED"
    return "IN_PROGRESS"
