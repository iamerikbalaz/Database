"""One-level Customers and Orders; all writes are actor-bound and recoverable."""
import base64
import binascii
import hashlib
import io
import re
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.auth.access import ADMIN, CATALOG_MANAGERS, AccessDependency
from app.customer_orders import customer_categories, customer_view, directory_snapshot, folder_prefix_for_customer, generated_order_name, legacy_order_status, order_view, prefetch_directory_states
from app.db.directory_models import DirectoryChangeEvent, DirectoryCommand
from app.db.models import Company, CompanyChangeEvent, InternalUser, PBRMaterial, Project, PublishedBrand, ResourceChangeEvent
from app.directory_schemas import CustomerCreate, CustomerFilters, CustomerLogo, CustomerUpdate, OrderCreate, OrderFilters, OrderUpdate
from app.material_review import canonical_hash
from app.material_identity import require_brand_idle
from app.material_table import utc
from app.resource_commands import CommandInput, CommandKey


def _get(session, model, identifier, *, lock=False):
    query = select(model).where(model.id == identifier)
    if model is PublishedBrand:
        query = query.where(PublishedBrand.is_customer.is_(True))
    item = session.scalar(query.with_for_update() if lock else query)
    if item is None:
        raise HTTPException(404, "Customer or order not found.")
    return item


def _require_key(key):
    if key is None or key.int == 0:
        raise HTTPException(422, "A nonzero Idempotency-Key is required.")


def _command_hash(kind, action, target, submitted):
    return canonical_hash({"kind": kind, "action": action, "target_id": str(target) if target else None, "payload": submitted})


def _saved_receipt(receipt):
    target = receipt.customer_id or receipt.order_id
    if not isinstance(receipt.response_snapshot, dict) or receipt.response_snapshot.get("id") != str(target) or canonical_hash(receipt.response_snapshot) != receipt.response_hash:
        raise HTTPException(503, {"code": "DIRECTORY_RECEIPT_INVALID"})
    return receipt.response_snapshot


def _replay(session, actor, key, kind, action, target, submitted):
    _require_key(key)
    receipt = session.scalar(select(DirectoryCommand).where(DirectoryCommand.actor_id == actor.id, DirectoryCommand.request_key == key))
    if receipt is None:
        return None
    if receipt.request_hash != _command_hash(kind, action, target, submitted):
        raise HTTPException(409, {"code": "DIRECTORY_COMMAND_KEY_REUSED"})
    return JSONResponse(_saved_receipt(receipt), status_code=201 if action == "CREATED" else 200, headers={"Idempotency-Replayed": "true"})


def _check_revision(item, expected):
    if utc(item.updated_at) != utc(expected):
        raise HTTPException(409, {"code": "DIRECTORY_RECORD_CHANGED", "message": "This record changed. Reload it before editing again."})


def _append_change(session, item, actor_id, before, action):
    session.flush()
    after = directory_snapshot(item)
    if before == after:
        return
    kind, column = ("CUSTOMER", "customer_id") if isinstance(item, PublishedBrand) else ("ORDER", "order_id")
    version = (session.scalar(select(func.max(DirectoryChangeEvent.version)).where(getattr(DirectoryChangeEvent, column) == item.id)) or 0) + 1
    session.add(DirectoryChangeEvent(kind=kind, **{column: item.id}, actor_id=actor_id, version=version, action=action,
        before_snapshot=before, after_snapshot=after, before_hash=canonical_hash(before), after_hash=canonical_hash(after)))


def _finish(session, item, actor, key, action, target, submitted, before, *, sync_enabled=False):
    kind, column, view = ("CUSTOMER", "customer_id", customer_view) if isinstance(item, PublishedBrand) else ("ORDER", "order_id", order_view)
    try:
        _append_change(session, item, actor.id, before, action)
        session.flush()
        response = view(session, item, sync_enabled=sync_enabled)
        session.add(DirectoryCommand(actor_id=actor.id, request_key=key, kind=kind, action=action,
            **{column: item.id}, request_hash=_command_hash(kind, action, target, submitted),
            response_snapshot=response, response_hash=canonical_hash(response)))
        session.commit()
        return JSONResponse(response, status_code=201 if action == "CREATED" else 200, headers={"Idempotency-Replayed": "false"})
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, {"code": "DIRECTORY_UNIQUE_CONFLICT", "message": "This number, identifier or Notion link already exists."}) from None


def _assign_responsible(session, order, identifier):
    if identifier is None:
        order.responsible_id = None
        order.responsible_notion_page_ids = []
        return
    person = session.get(InternalUser, identifier)
    if person is None or not person.is_active or person.role != "PROCESSOR":
        raise HTTPException(409, "Responsible person must be an active processor.")
    order.responsible_id = person.id
    order.responsible_notion_page_ids = [person.notion_people_page_id] if person.notion_people_page_id else []


def _update_generated_name(session, order):
    customer = session.get(PublishedBrand, order.customer_id) if order.customer_id else None
    if customer and order.project_type and order.starting_date:
        generated = generated_order_name(order.project_number, customer.name, order.project_type, order.starting_date)
        if len(generated) > 255:
            raise HTTPException(422, "The generated order name must fit within 255 characters.")
        from app.order_folders import OrderFolderError, validate_folder_name
        try:
            validate_folder_name(generated)
        except OrderFolderError:
            raise HTTPException(422, "The generated order name contains characters that cannot be used in a Windows folder name.") from None
        order.name = generated


def _folder_idle(session, order):
    from app.order_folders import folder_status
    if folder_status(session, order.id).get("status") in {"RUNNING", "RECONCILE"}:
        raise HTTPException(409, {"code": "ORDER_FOLDER_OPERATION_RUNNING"})


def _next_number(session, settings):
    existing = set(session.scalars(select(Project.project_number)))
    if settings.order_folders_enabled:
        from app.order_folders import OrderFolderError, known_folder_numbers
        try:
            existing |= known_folder_numbers(settings.order_folders_root)
        except OrderFolderError as error:
            raise HTTPException(503, {"code": error.code}) from None
    numbers = [int(value) for value in existing if re.fullmatch(r"\d{4}", value)]
    number = max(numbers, default=0) + 1
    if number > 9999:
        raise HTTPException(409, {"code": "ORDER_NUMBERS_EXHAUSTED"})
    return f"{number:04d}", existing


def build_directory_router(database, settings):
    router = APIRouter(prefix="/api", tags=["customers and orders"])

    @router.get("/directory-commands/{request_key}")
    def recover(request_key: UUID, access: AccessDependency):
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            receipt = session.scalar(select(DirectoryCommand).where(DirectoryCommand.actor_id == actor.id, DirectoryCommand.request_key == request_key))
            if receipt is None:
                raise HTTPException(404, {"code": "DIRECTORY_COMMAND_NOT_FOUND"})
            return {"request_key": str(request_key), "kind": receipt.kind, "resource_id": str(receipt.customer_id or receipt.order_id),
                "request_hash": receipt.request_hash, "response_hash": receipt.response_hash, "response": _saved_receipt(receipt)}

    @router.get("/customers")
    def customers(filters: Annotated[CustomerFilters, Query()], access: AccessDependency):
        with database.session() as session:
            access.check(session)
            query = select(PublishedBrand).where(PublishedBrand.is_customer.is_(True))
            if filters.search:
                query = query.where(or_(*(getattr(PublishedBrand, key).icontains(filters.search, autoescape=True) for key in ("name", "customer_brand_identifier", "legal_name", "address", "shipping_address", "website", "vat_id", "description", "notes"))))
            if filters.status:
                query = query.where(PublishedBrand.customer_status == filters.status)
            if filters.is_active is not None:
                query = query.where(PublishedBrand.is_active == filters.is_active)
            if filters.main_category_code:
                query = query.where(PublishedBrand.id.in_(select(PBRMaterial.published_brand_id).where(PBRMaterial.main_category_code == filters.main_category_code)))
            rows = list(session.scalars(query.order_by(PublishedBrand.name, PublishedBrand.id)))
            categories = customer_categories(session, [row.id for row in rows])
            prefetch_directory_states(session, "CUSTOMER", [row.id for row in rows])
            return [customer_view(session, row, categories[row.id], sync_enabled=settings.notion_outbound_enabled) for row in rows]

    @router.post("/customers", status_code=201)
    def create_customer(payload: CustomerCreate, access: AccessDependency, submitted: CommandInput, request_key: CommandKey = None):
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS, exclusive=True)
            if (replayed := _replay(session, actor, request_key, "CUSTOMER", "CREATED", None, submitted)) is not None:
                return replayed
            values = payload.model_dump(exclude={"folder_prefix", "brand_identifier", "status"})
            if values.get("website") is not None:
                values["website"] = str(values["website"])
            company = Company(name=payload.name)
            session.add(company)
            session.flush()
            prefix = payload.folder_prefix or folder_prefix_for_customer(payload.name)
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{0,254}", prefix):
                raise HTTPException(422, "Folder prefix must contain uppercase letters, numbers and hyphens.")
            customer = PublishedBrand(company_id=company.id, **values, folder_prefix=prefix,
                customer_status=payload.status, customer_brand_identifier=payload.brand_identifier,
                brand_identifier=payload.brand_identifier or "unassigned-" + uuid4().hex)
            session.add(customer)
            try:
                session.flush()
            except IntegrityError:
                raise HTTPException(409, "Customer identifier or folder prefix already exists.") from None
            from app.notion_outbound import enqueue_customer_sync
            enqueue_customer_sync(session, customer, actor.id)
            return _finish(session, customer, actor, request_key, "CREATED", None, submitted, {}, sync_enabled=settings.notion_outbound_enabled)

    @router.get("/customers/{customer_id}")
    def customer(customer_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            return customer_view(session, _get(session, PublishedBrand, customer_id), sync_enabled=settings.notion_outbound_enabled)

    @router.patch("/customers/{customer_id}")
    def update_customer(customer_id: UUID, payload: CustomerUpdate, access: AccessDependency, submitted: CommandInput, request_key: CommandKey = None):
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS, exclusive=True)
            if (replayed := _replay(session, actor, request_key, "CUSTOMER", "UPDATED", customer_id, submitted)) is not None:
                return replayed
            item = _get(session, PublishedBrand, customer_id, lock=True)
            _check_revision(item, payload.expected_updated_at)
            require_brand_idle(session, customer_id)
            before = directory_snapshot(item)
            changes = payload.model_dump(exclude_unset=True, exclude={"expected_updated_at"})
            from app.notion_outbound import enqueue_customer_sync, enqueue_order_sync
            if changes.get("brand_identifier"):
                identifier = changes["brand_identifier"]
                collision = session.scalar(select(PublishedBrand.id).where(PublishedBrand.id != item.id,
                    or_(PublishedBrand.brand_identifier == identifier, PublishedBrand.customer_brand_identifier == identifier)).limit(1))
                if collision is not None:
                    raise HTTPException(409, {"code": "DIRECTORY_UNIQUE_CONFLICT", "message": "This Brand Identifier is already assigned to another customer."})
            related_orders = []
            if "name" in changes and changes["name"] != item.name:
                if session.scalar(select(PBRMaterial.id).where(PBRMaterial.published_brand_id == item.id, PBRMaterial.folder_path.is_not(None)).limit(1)) is not None:
                    raise HTTPException(409, {"code": "BRAND_SOURCE_REWRITE_REQUIRED", "message": "Renaming this customer requires a controlled update of its linked materials and metadata.json manufacturer values."})
                related_orders = list(session.scalars(select(Project).where(Project.customer_id == item.id).order_by(Project.id).with_for_update()))
                for order in related_orders:
                    _folder_idle(session, order)
            for key, value in changes.items():
                key = {"status": "customer_status", "brand_identifier": "customer_brand_identifier"}.get(key, key)
                setattr(item, key, str(value) if key == "website" and value is not None else value)
            if "brand_identifier" in changes and changes["brand_identifier"]:
                item.brand_identifier = changes["brand_identifier"]
            if directory_snapshot(item) != before:
                item.updated_at = datetime.now(UTC)
                if set(changes) & {"name", "brand_identifier", "is_active"}:
                    from app.material_review import invalidate_review
                    for material in session.scalars(select(PBRMaterial).where(PBRMaterial.published_brand_id == item.id).order_by(PBRMaterial.id).with_for_update()):
                        invalidate_review(session, material, actor.id, "BRAND_FIELDS_CHANGED")
                enqueue_customer_sync(session, item, actor.id)
                for order in related_orders:
                    order_before = directory_snapshot(order)
                    _update_generated_name(session, order)
                    if directory_snapshot(order) != order_before:
                        order.updated_at = datetime.now(UTC)
                        _append_change(session, order, actor.id, order_before, "UPDATED")
                        enqueue_order_sync(session, order, actor.id)
            return _finish(session, item, actor, request_key, "UPDATED", customer_id, submitted, before, sync_enabled=settings.notion_outbound_enabled)

    @router.get("/customers/{customer_id}/logo")
    def logo(customer_id: UUID, access: AccessDependency, request: Request):
        with database.session() as session:
            access.check(session)
            item = _get(session, PublishedBrand, customer_id)
            if not item.logo_sha256 or not item.logo_content:
                raise HTTPException(404, "Customer logo not found.")
            headers = {"Cache-Control": "private, max-age=3600", "ETag": f'"{item.logo_sha256}"', "X-Content-Type-Options": "nosniff"}
            if request.headers.get("if-none-match") == headers["ETag"]:
                return Response(status_code=304, headers=headers)
            return Response(item.logo_content, media_type=item.logo_content_type, headers=headers)

    @router.put("/customers/{customer_id}/logo")
    def upload_logo(customer_id: UUID, payload: CustomerLogo, access: AccessDependency, submitted: CommandInput, request_key: CommandKey = None):
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS, exclusive=True)
            if (replayed := _replay(session, actor, request_key, "CUSTOMER", "LOGO_UPDATED", customer_id, submitted)) is not None:
                return replayed
            item = _get(session, PublishedBrand, customer_id, lock=True)
            _check_revision(item, payload.expected_updated_at)
            before = directory_snapshot(item)
            try:
                content = base64.b64decode(payload.content_base64, validate=True)
                if not 0 < len(content) <= 2 * 1024 * 1024:
                    raise ValueError("Invalid logo size")
                from PIL import Image
                with Image.open(io.BytesIO(content)) as image:
                    if image.format not in {"PNG", "JPEG", "WEBP"} or max(image.size) > 4096 or image.width * image.height > 16000000 or getattr(image, "n_frames", 1) != 1:
                        raise ValueError("Invalid logo image")
                    mime = Image.MIME[image.format]
                    image.verify()
            except ImportError:
                raise HTTPException(503, "Logo upload requires the desktop image decoder.") from None
            except (binascii.Error, ValueError, OSError, Image.DecompressionBombError):
                raise HTTPException(422, "Use a valid PNG, JPEG or WebP logo, at most 2 MB and 4096 pixels per side.") from None
            item.logo_content, item.logo_content_type = content, mime
            item.logo_filename = payload.filename.replace("\\", "/").rsplit("/", 1)[-1]
            item.logo_sha256 = hashlib.sha256(content).hexdigest()
            if directory_snapshot(item) != before:
                item.updated_at = datetime.now(UTC)
            return _finish(session, item, actor, request_key, "LOGO_UPDATED", customer_id, submitted, before, sync_enabled=settings.notion_outbound_enabled)

    @router.get("/orders")
    def orders(filters: Annotated[OrderFilters, Query()], access: AccessDependency):
        with database.session() as session:
            access.check(session)
            query = select(Project).outerjoin(PublishedBrand, Project.customer_id == PublishedBrand.id)
            if filters.search:
                query = query.where(or_(*(field.icontains(filters.search, autoescape=True) for field in (Project.name, Project.project_number, Project.project_type, Project.notes, Project.folder_path, PublishedBrand.name))))
            for key, field in (("customer_id", Project.customer_id), ("status", Project.order_status), ("priority", Project.priority), ("responsible_id", Project.responsible_id)):
                if getattr(filters, key) is not None:
                    query = query.where(field == getattr(filters, key))
            for key, field, after in (("starting_from", Project.starting_date, True), ("starting_to", Project.starting_date, False), ("due_from", Project.due_date, True), ("due_to", Project.due_date, False)):
                if getattr(filters, key) is not None:
                    query = query.where(field >= getattr(filters, key) if after else field <= getattr(filters, key))
            rows = list(session.scalars(query.order_by(Project.project_number.desc(), Project.id)))
            prefetch_directory_states(session, "ORDER", [row.id for row in rows])
            session.info["directory_customers"] = {customer.id: customer for customer in session.scalars(select(PublishedBrand).where(PublishedBrand.id.in_({row.customer_id for row in rows if row.customer_id})))}
            return [order_view(session, row, sync_enabled=settings.notion_outbound_enabled) for row in rows]

    @router.post("/orders", status_code=201)
    def create_order(payload: OrderCreate, access: AccessDependency, submitted: CommandInput, request_key: CommandKey = None):
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS, exclusive=True)
            if (replayed := _replay(session, actor, request_key, "ORDER", "CREATED", None, submitted)) is not None:
                return replayed
            customer = _get(session, PublishedBrand, payload.customer_id)
            number, existing = _next_number(session, settings)
            number = payload.number or number
            if number in existing:
                raise HTTPException(409, "Order number already exists in the application or project folders.")
            order = Project(company_id=customer.company_id, customer_id=customer.id, project_number=number, name="",
                project_type=payload.project_type, starting_date=payload.starting_date, due_date=payload.due_date,
                notes=payload.notes, order_status=payload.status, priority=payload.priority, status=legacy_order_status(payload.status))
            _assign_responsible(session, order, payload.responsible_id)
            _update_generated_name(session, order)
            session.add(order)
            session.flush()
            from app.notion_outbound import enqueue_order_sync
            from app.order_folders import enqueue_order_folder_create
            enqueue_order_sync(session, order, actor.id, responsible_changed=payload.responsible_id is not None)
            enqueue_order_folder_create(session, order, actor.id)
            return _finish(session, order, actor, request_key, "CREATED", None, submitted, {}, sync_enabled=settings.notion_outbound_enabled)

    @router.get("/orders/{order_id}")
    def order(order_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            return order_view(session, _get(session, Project, order_id), sync_enabled=settings.notion_outbound_enabled)

    @router.patch("/orders/{order_id}")
    def update_order(order_id: UUID, payload: OrderUpdate, access: AccessDependency, submitted: CommandInput, request_key: CommandKey = None):
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS, exclusive=True)
            if (replayed := _replay(session, actor, request_key, "ORDER", "UPDATED", order_id, submitted)) is not None:
                return replayed
            item = _get(session, Project, order_id, lock=True)
            _check_revision(item, payload.expected_updated_at)
            before = directory_snapshot(item)
            changes = payload.model_dump(exclude_unset=True, exclude={"expected_updated_at"})
            if set(changes) & {"number", "customer_id", "project_type", "starting_date"}:
                _folder_idle(session, item)
            if "customer_id" in changes:
                customer = _get(session, PublishedBrand, changes["customer_id"])
                item.company_id = customer.company_id
            if "responsible_id" in changes:
                _assign_responsible(session, item, changes.pop("responsible_id"))
            if "number" in changes and changes["number"] != item.project_number:
                _, existing = _next_number(session, settings)
                if changes["number"] in existing:
                    raise HTTPException(409, "Order number already exists in the application or project folders.")
            for key, value in changes.items():
                setattr(item, {"number": "project_number", "status": "order_status"}.get(key, key), value)
            item.status = legacy_order_status(item.order_status)
            _update_generated_name(session, item)
            if directory_snapshot(item) != before:
                item.updated_at = datetime.now(UTC)
                from app.notion_outbound import enqueue_order_sync
                enqueue_order_sync(session, item, actor.id, responsible_changed="responsible_id" in payload.model_fields_set)
            return _finish(session, item, actor, request_key, "UPDATED", order_id, submitted, before, sync_enabled=settings.notion_outbound_enabled)

    def history(kind, identifier, access):
        with database.session() as session:
            access.check(session, ADMIN)
            model, new_field, old_field = (PublishedBrand, "customer_id", "brand_id") if kind == "CUSTOMER" else (Project, "order_id", "project_id")
            resource = _get(session, model, identifier)
            new_events = list(session.scalars(select(DirectoryChangeEvent).where(getattr(DirectoryChangeEvent, new_field) == identifier).order_by(DirectoryChangeEvent.created_at.desc()).limit(200)))
            old_events = list(session.scalars(select(ResourceChangeEvent).where(getattr(ResourceChangeEvent, old_field) == identifier).order_by(ResourceChangeEvent.created_at.desc()).limit(200)))
            company_events = list(session.scalars(select(CompanyChangeEvent).where(CompanyChangeEvent.company_id == resource.company_id).order_by(CompanyChangeEvent.created_at.desc()).limit(200))) if kind == "CUSTOMER" else []
            events = sorted(new_events + old_events + company_events, key=lambda row: (utc(row.created_at), str(row.id)), reverse=True)
            actor_ids = {row.actor_id for row in events}
            authors = {row.id: row.display_name for row in session.scalars(select(InternalUser).where(InternalUser.id.in_(actor_ids)))}
            items = []
            for row in events[:200]:
                if canonical_hash(row.before_snapshot) != row.before_hash or canonical_hash(row.after_snapshot) != row.after_hash:
                    raise HTTPException(503, {"code": "DIRECTORY_HISTORY_INVALID"})
                items.append({"id": row.id, "actor_id": row.actor_id, "actor_name": authors.get(row.actor_id), "action": row.action,
                    "before": row.before_snapshot, "after": row.after_snapshot, "created_at": row.created_at,
                    "source": "LEGACY_COMPANY" if isinstance(row, CompanyChangeEvent) else row.kind})
            return jsonable_encoder({"items": items})

    @router.get("/customers/{customer_id}/history")
    def customer_history(customer_id: UUID, access: AccessDependency):
        return history("CUSTOMER", customer_id, access)

    @router.get("/orders/{order_id}/history")
    def order_history(order_id: UUID, access: AccessDependency):
        return history("ORDER", order_id, access)

    return router
