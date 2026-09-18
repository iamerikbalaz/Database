from contextlib import AbstractContextManager
from typing import Annotated, Any, Protocol, TypeVar
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.access import AccessDependency, ADMIN, CATALOG_MANAGERS, MATERIAL_EDITORS
from app.auth.service import database_now, lock_user_credential, revoke_all_user_sessions
from app.company_history import append_company_change, company_snapshot
from app.resource_history import append_resource_change, resource_snapshot
from app.material_identity import identity_context, require_brand_idle, require_material_idle

from app.db.models import (
    Company,
    InternalUser,
    MaterialNumberReservation,
    MaterialIdentityHistory,
    MaterialPublicationStatus,
    MaterialValidationStatus,
    MaterialWorkflowStatus,
    PBRMaterial,
    PBRMaterialMetadata,
    PBRMaterialMetadataSnapshot,
    Project,
    PublishedBrand,
)
from app.schemas import (
    CompanyCreate,
    CompanyListFilters,
    CompanyRead,
    CompanyUpdate,
    InternalUserCreate,
    InternalUserListFilters,
    InternalUserRead,
    InternalUserUpdate,
    PBRMaterialCreate,
    PBRMaterialListFilters,
    PBRMaterialMetadataRead,
    PBRMaterialMetadataSnapshotRead,
    PBRMaterialRead,
    PBRMaterialUpdate,
    ProjectCreate,
    ProjectListFilters,
    ProjectRead,
    ProjectUpdate,
    PublishedBrandCreate,
    PublishedBrandListFilters,
    PublishedBrandRead,
    PublishedBrandUpdate,
)


class SessionDatabase(Protocol):
    def session(self) -> AbstractContextManager[Session]: ...


ModelT = TypeVar("ModelT", Company, PublishedBrand, Project, InternalUser, PBRMaterial)


def _get_or_404(session: Session, model: type[ModelT], item_id: UUID, label: str) -> ModelT:
    item = session.get(model, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{label} not found.",
        )
    return item


def _require_company(session: Session, company_id: UUID) -> None:
    _get_or_404(session, Company, company_id, "Company")


def _require_active_internal_user(session: Session, user_id: UUID) -> InternalUser:
    user = _get_or_404(session, InternalUser, user_id, "Internal user")
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Internal user is inactive.",
        )
    if user.role != "PROCESSOR":
        raise HTTPException(409, "Assigned user must have the PROCESSOR role.")
    return user


def _technical_identity(
    brand: PublishedBrand,
    sequence_number: int,
    main_category_code: str,
) -> str:
    return f"{brand.folder_prefix}_{sequence_number:04d}_{main_category_code}"


def _ensure_unique(
    session: Session,
    model: type[ModelT],
    column: Any,
    value: Any,
    field_name: str,
    current_id: UUID | None = None,
) -> None:
    statement = select(model.id).where(column == value)
    if current_id is not None:
        statement = statement.where(model.id != current_id)
    if session.scalar(statement.limit(1)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{field_name} already exists.",
        )


def _values(schema: BaseModel, *, exclude_unset: bool = False) -> dict[str, Any]:
    values = schema.model_dump(exclude_unset=exclude_unset)
    if values.get("website") is not None:
        values["website"] = str(values["website"])
    return values


def _commit(session: Session, item: ModelT) -> ModelT:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A record with one of the unique values already exists.",
        ) from exc
    session.refresh(item)
    return item


def _apply_update(item: ModelT, values: dict[str, Any]) -> None:
    for field_name, value in values.items():
        setattr(item, field_name, value)


def _commit_company(session, company, actor_id, before, *, action="UPDATED"):
    try:
        append_company_change(session, company, actor_id, before, action=action)
        return _commit(session, company)
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "A record with one of the unique values already exists.") from None


def _commit_resource(session, item, actor_id, before, *, action="UPDATED"):
    try:
        append_resource_change(session, item, actor_id, before, action=action)
        return _commit(session, item)
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "A record with one of the unique values already exists.") from None


def build_resources_router(database: SessionDatabase) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/companies", response_model=list[CompanyRead], tags=["companies"])
    def list_companies(filters: Annotated[CompanyListFilters, Query()], access: AccessDependency) -> list[Company]:
        with database.session() as session:
            access.check(session)
            statement = select(Company)
            if filters.search is not None:
                statement = statement.where(Company.name.icontains(filters.search, autoescape=True))
            if filters.is_active is not None:
                statement = statement.where(Company.is_active == filters.is_active)
            return list(session.scalars(statement.order_by(Company.created_at, Company.id)))

    @router.post(
        "/companies",
        response_model=CompanyRead,
        status_code=status.HTTP_201_CREATED,
        tags=["companies"],
    )
    def create_company(payload: CompanyCreate, access: AccessDependency) -> Company:
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            if payload.notion_page_id is not None:
                _ensure_unique(
                    session,
                    Company,
                    Company.notion_page_id,
                    payload.notion_page_id,
                    "notion_page_id",
                )
            company = Company(**_values(payload))
            session.add(company)
            return _commit_company(session, company, actor.id, {}, action="CREATED")

    @router.get("/companies/{company_id}", response_model=CompanyRead, tags=["companies"])
    def get_company(company_id: UUID, access: AccessDependency) -> Company:
        with database.session() as session:
            access.check(session)
            return _get_or_404(session, Company, company_id, "Company")

    @router.patch("/companies/{company_id}", response_model=CompanyRead, tags=["companies"])
    def update_company(company_id: UUID, payload: CompanyUpdate, access: AccessDependency) -> Company:
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            company = session.scalar(select(Company).where(Company.id == company_id).with_for_update())
            if company is None: raise HTTPException(404, "Company not found.")
            before = company_snapshot(company)
            values = _values(payload, exclude_unset=True)
            if values.get("notion_page_id") is not None:
                _ensure_unique(
                    session,
                    Company,
                    Company.notion_page_id,
                    values["notion_page_id"],
                    "notion_page_id",
                    company.id,
                )
            _apply_update(company, values)
            return _commit_company(session, company, actor.id, before)

    @router.get("/brands", response_model=list[PublishedBrandRead], tags=["brands"])
    def list_brands(
        filters: Annotated[PublishedBrandListFilters, Query()], access: AccessDependency) -> list[PublishedBrand]:
        with database.session() as session:
            access.check(session)
            statement = select(PublishedBrand)
            if filters.company_id is not None:
                statement = statement.where(PublishedBrand.company_id == filters.company_id)
            if filters.is_active is not None:
                statement = statement.where(PublishedBrand.is_active == filters.is_active)
            if filters.search is not None:
                statement = statement.where(
                    or_(
                        PublishedBrand.name.icontains(filters.search, autoescape=True),
                        PublishedBrand.brand_identifier.icontains(
                            filters.search,
                            autoescape=True,
                        ),
                    )
                )
            return list(
                session.scalars(statement.order_by(PublishedBrand.created_at, PublishedBrand.id))
            )

    @router.post(
        "/brands",
        response_model=PublishedBrandRead,
        status_code=status.HTTP_201_CREATED,
        tags=["brands"],
    )
    def create_brand(payload: PublishedBrandCreate, access: AccessDependency) -> PublishedBrand:
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            _require_company(session, payload.company_id)
            _ensure_unique(
                session,
                PublishedBrand,
                PublishedBrand.folder_prefix,
                payload.folder_prefix,
                "folder_prefix",
            )
            _ensure_unique(
                session,
                PublishedBrand,
                PublishedBrand.brand_identifier,
                payload.brand_identifier,
                "brand_identifier",
            )
            brand = PublishedBrand(**_values(payload))
            session.add(brand)
            return _commit_resource(session, brand, actor.id, {}, action="CREATED")

    @router.get("/brands/{brand_id}", response_model=PublishedBrandRead, tags=["brands"])
    def get_brand(brand_id: UUID, access: AccessDependency) -> PublishedBrand:
        with database.session() as session:
            access.check(session)
            return _get_or_404(session, PublishedBrand, brand_id, "Published brand")

    @router.patch("/brands/{brand_id}", response_model=PublishedBrandRead, tags=["brands"])
    def update_brand(brand_id: UUID, payload: PublishedBrandUpdate, access: AccessDependency) -> PublishedBrand:
        with database.session() as session:
            # Brand changes affect many materials. Use the same exclusive gate
            # as catalog retirement before locking the brand and its materials.
            actor = access.check(session, CATALOG_MANAGERS, exclusive=True)
            values = _values(payload, exclude_unset=True)
            brand = session.scalar(select(PublishedBrand).where(PublishedBrand.id == brand_id).with_for_update())
            if brand is None:
                raise HTTPException(404, "Published brand not found.")
            before = resource_snapshot(brand)
            require_brand_idle(session, brand_id)
            if "company_id" in values:
                _require_company(session, values["company_id"])
            if (
                "folder_prefix" in values
                and values["folder_prefix"] != brand.folder_prefix
                and session.scalar(
                    select(PBRMaterial.id)
                    .where(PBRMaterial.published_brand_id == brand.id)
                    .limit(1)
                )
                is not None
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="folder_prefix cannot be changed after materials have been created.",
                )
            for field_name in ("folder_prefix", "brand_identifier"):
                if field_name in values:
                    _ensure_unique(
                        session,
                        PublishedBrand,
                        getattr(PublishedBrand, field_name),
                        values[field_name],
                        field_name,
                        brand.id,
                    )
            if any(getattr(brand, key) != value for key, value in values.items()):
                from app.material_review import invalidate_review
                for material in session.scalars(select(PBRMaterial).where(PBRMaterial.published_brand_id == brand.id)
                        .order_by(PBRMaterial.id).with_for_update()):
                    invalidate_review(session, material, actor.id, "BRAND_FIELDS_CHANGED")
            _apply_update(brand, values)
            return _commit_resource(session, brand, actor.id, before)

    @router.get("/projects", response_model=list[ProjectRead], tags=["projects"])
    def list_projects(filters: Annotated[ProjectListFilters, Query()], access: AccessDependency) -> list[Project]:
        with database.session() as session:
            access.check(session)
            statement = select(Project)
            if filters.company_id is not None:
                statement = statement.where(Project.company_id == filters.company_id)
            if filters.status is not None:
                statement = statement.where(Project.status == filters.status.value)
            if filters.search is not None:
                statement = statement.where(
                    or_(
                        Project.project_number.icontains(filters.search, autoescape=True),
                        Project.name.icontains(filters.search, autoescape=True),
                    )
                )
            return list(session.scalars(statement.order_by(Project.created_at, Project.id)))

    @router.post(
        "/projects",
        response_model=ProjectRead,
        status_code=status.HTTP_201_CREATED,
        tags=["projects"],
    )
    def create_project(payload: ProjectCreate, access: AccessDependency) -> Project:
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            _require_company(session, payload.company_id)
            _ensure_unique(
                session,
                Project,
                Project.project_number,
                payload.project_number,
                "project_number",
            )
            project = Project(**_values(payload))
            session.add(project)
            return _commit_resource(session, project, actor.id, {}, action="CREATED")

    @router.get("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
    def get_project(project_id: UUID, access: AccessDependency) -> Project:
        with database.session() as session:
            access.check(session)
            return _get_or_404(session, Project, project_id, "Project")

    @router.patch("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
    def update_project(project_id: UUID, payload: ProjectUpdate, access: AccessDependency) -> Project:
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if project is None: raise HTTPException(404, "Project not found.")
            before = resource_snapshot(project)
            values = _values(payload, exclude_unset=True)
            if "company_id" in values:
                _require_company(session, values["company_id"])
            if "project_number" in values:
                _ensure_unique(
                    session,
                    Project,
                    Project.project_number,
                    values["project_number"],
                    "project_number",
                    project.id,
                )
            _apply_update(project, values)
            return _commit_resource(session, project, actor.id, before)

    @router.get(
        "/internal-users",
        response_model=list[InternalUserRead],
        tags=["internal-users"],
    )
    def list_internal_users(
        filters: Annotated[InternalUserListFilters, Query()], access: AccessDependency) -> list[InternalUser]:
        with database.session() as session:
            access.check(session)
            statement = select(InternalUser)
            if access.user.role == "PROCESSOR":
                statement = statement.where(InternalUser.id == access.user.id)
            if filters.role is not None:
                statement = statement.where(InternalUser.role == filters.role)
            if filters.is_active is not None:
                statement = statement.where(InternalUser.is_active == filters.is_active)
            if filters.search is not None:
                statement = statement.where(
                    or_(
                        InternalUser.display_name.icontains(filters.search, autoescape=True),
                        InternalUser.email.icontains(filters.search, autoescape=True),
                    )
                )
            return list(
                session.scalars(statement.order_by(InternalUser.created_at, InternalUser.id))
            )

    @router.post(
        "/internal-users",
        response_model=InternalUserRead,
        status_code=status.HTTP_201_CREATED,
        tags=["internal-users"],
    )
    def create_internal_user(payload: InternalUserCreate, access: AccessDependency) -> InternalUser:
        with database.session() as session:
            actor = access.check(session, ADMIN, exclusive=True)
            _ensure_unique(
                session,
                InternalUser,
                InternalUser.email,
                payload.email,
                "email",
            )
            user = InternalUser(**_values(payload))
            session.add(user)
            return _commit_resource(session, user, actor.id, {}, action="CREATED")

    @router.get(
        "/internal-users/{user_id}",
        response_model=InternalUserRead,
        tags=["internal-users"],
    )
    def get_internal_user(user_id: UUID, access: AccessDependency) -> InternalUser:
        with database.session() as session:
            access.check(session)
            if access.user.role == "PROCESSOR" and user_id != access.user.id:
                raise HTTPException(404, "Internal user not found.")
            return _get_or_404(session, InternalUser, user_id, "Internal user")

    @router.patch(
        "/internal-users/{user_id}",
        response_model=InternalUserRead,
        tags=["internal-users"],
    )
    def update_internal_user(user_id: UUID, payload: InternalUserUpdate, access: AccessDependency) -> InternalUser:
        with database.session() as session:
            actor = access.check(session, ADMIN, exclusive=True)
            lock_user_credential(session, user_id)
            user = session.scalar(select(InternalUser).where(InternalUser.id == user_id).with_for_update())
            if user is None: raise HTTPException(404, "Internal user not found.")
            before = resource_snapshot(user)
            values = _values(payload, exclude_unset=True)
            if user.id == access.user.id and any(
                field in values and values[field] != getattr(user, field)
                for field in ("role", "is_active")
            ):
                raise HTTPException(409, "Use another administrator to change your own role or active state.")
            if any(field in values and values[field] != getattr(user, field)
                   for field in ("email", "role", "is_active")):
                revoke_all_user_sessions(session, user.id, database_now(session))
            if "email" in values:
                _ensure_unique(
                    session,
                    InternalUser,
                    InternalUser.email,
                    values["email"],
                    "email",
                    user.id,
                )
            _apply_update(user, values)
            return _commit_resource(session, user, actor.id, before)

    @router.get("/materials", response_model=list[PBRMaterialRead], tags=["materials"])
    def list_materials(
        filters: Annotated[PBRMaterialListFilters, Query()], access: AccessDependency) -> list[PBRMaterial]:
        with database.session() as session:
            access.check(session)
            statement = select(PBRMaterial)
            if access.user.role == "PROCESSOR":
                statement = statement.where(PBRMaterial.assigned_processor_id == access.user.id)
            for field_name in (
                "project_id",
                "published_brand_id",
                "assigned_processor_id",
                "main_category_code",
                "workflow_status",
                "validation_status",
                "publication_status",
                "is_published",
            ):
                value = getattr(filters, field_name)
                if value is not None:
                    statement = statement.where(getattr(PBRMaterial, field_name) == value)
            if filters.search is not None:
                statement = statement.where(
                    or_(
                        PBRMaterial.material_name.icontains(filters.search, autoescape=True),
                        PBRMaterial.technical_identity.icontains(
                            filters.search,
                            autoescape=True,
                        ),
                    )
                )
            return list(
                session.scalars(statement.order_by(PBRMaterial.created_at, PBRMaterial.id))
            )

    @router.post(
        "/materials",
        response_model=PBRMaterialRead,
        status_code=status.HTTP_201_CREATED,
        tags=["materials"],
    )
    def create_material(payload: PBRMaterialCreate, access: AccessDependency) -> PBRMaterial:
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            _get_or_404(session, Project, payload.project_id, "Project")
            _require_active_internal_user(session, payload.assigned_processor_id)
            brand = session.scalar(
                select(PublishedBrand)
                .where(PublishedBrand.id == payload.published_brand_id)
                .with_for_update()
            )
            if brand is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Published brand not found.",
                )

            sequence_number = brand.next_sequence_number
            if sequence_number > 9999:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Published brand sequence is exhausted.",
                )

            values = _values(payload)
            material = PBRMaterial(
                **values,
                sequence_number=sequence_number,
                technical_identity=_technical_identity(
                    brand,
                    sequence_number,
                    payload.main_category_code,
                ),
                folder_path=None,
                workflow_status=MaterialWorkflowStatus.IN_PROGRESS.value,
                validation_status=MaterialValidationStatus.NOT_CHECKED.value,
                is_published=False,
                publication_status=MaterialPublicationStatus.NOT_PUBLISHED.value,
            )
            material.metadata_state = PBRMaterialMetadata()
            brand.next_sequence_number = sequence_number + 1
            session.add(material)
            session.flush()
            session.add(MaterialNumberReservation(brand_id=brand.id, sequence_number=sequence_number,
                material_id=material.id, actor_id=access.user.id if session.get(InternalUser, access.user.id) else None))
            return _commit_resource(session, material, actor.id, {}, action="CREATED")

    @router.get("/materials/{material_id}", response_model=PBRMaterialRead, tags=["materials"])
    def get_material(material_id: UUID, access: AccessDependency) -> PBRMaterial:
        with database.session() as session:
            access.check(session)
            material = _get_or_404(session, PBRMaterial, material_id, "PBR material")
            access.require_material(material)
            return material

    @router.get(
        "/materials/{material_id}/metadata",
        response_model=PBRMaterialMetadataRead,
        tags=["materials"],
    )
    def get_material_metadata(material_id: UUID, access: AccessDependency) -> PBRMaterialMetadata:
        with database.session() as session:
            access.check(session)
            access.require_material(_get_or_404(session, PBRMaterial, material_id, "PBR material"))
            metadata = session.get(PBRMaterialMetadata, material_id)
            if metadata is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="PBR material metadata state is missing.",
                )
            return metadata

    @router.get(
        "/materials/{material_id}/metadata/snapshots",
        response_model=list[PBRMaterialMetadataSnapshotRead],
        tags=["materials"],
    )
    def list_material_metadata_snapshots(
        material_id: UUID, access: AccessDependency) -> list[PBRMaterialMetadataSnapshot]:
        with database.session() as session:
            access.check(session)
            access.require_material(_get_or_404(session, PBRMaterial, material_id, "PBR material"))
            statement = (
                select(PBRMaterialMetadataSnapshot)
                .where(PBRMaterialMetadataSnapshot.material_id == material_id)
                .order_by(
                    PBRMaterialMetadataSnapshot.sequence_number,
                    PBRMaterialMetadataSnapshot.id,
                )
            )
            return list(session.scalars(statement))

    @router.patch(
        "/materials/{material_id}",
        response_model=PBRMaterialRead,
        tags=["materials"],
    )
    def update_material(material_id: UUID, payload: PBRMaterialUpdate, access: AccessDependency) -> PBRMaterial:
        with database.session() as session:
            actor = access.check(session, MATERIAL_EDITORS)
            material = session.scalar(
                select(PBRMaterial)
                .where(PBRMaterial.id == material_id)
                .with_for_update()
            )
            if material is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="PBR material not found.",
                )
            access.require_material(material)
            values = _values(payload, exclude_unset=True)
            require_material_idle(session, material_id)
            before = resource_snapshot(material)
            old_identity = identity_context(material)
            if access.user.role == "PROCESSOR" and set(values) - {"material_name"}:
                raise HTTPException(403, "Only a production lead or administrator can change material assignment or identity.")
            if "project_id" in values:
                _get_or_404(session, Project, values["project_id"], "Project")
            if "assigned_processor_id" in values:
                _require_active_internal_user(session, values["assigned_processor_id"])
            if (
                "main_category_code" in values
                and values["main_category_code"] != material.main_category_code
            ):
                if material.folder_path is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "main_category_code cannot be changed while folder_path is set."
                        ),
                    )
                brand = _get_or_404(
                    session,
                    PublishedBrand,
                    material.published_brand_id,
                    "Published brand",
                )
                technical_identity = _technical_identity(
                    brand,
                    material.sequence_number,
                    values["main_category_code"],
                )
                _ensure_unique(
                    session,
                    PBRMaterial,
                    PBRMaterial.technical_identity,
                    technical_identity,
                    "technical_identity",
                    material.id,
                )
                material.technical_identity = technical_identity
            if any(getattr(material, key) != value for key, value in values.items()):
                from app.material_review import invalidate_review
                invalidate_review(session, material, access.user.id, "MATERIAL_FIELDS_CHANGED")
            _apply_update(material, values)
            if material.technical_identity != old_identity["technical_identity"]:
                session.add(MaterialIdentityHistory(material_id=material.id,
                    actor_id=access.user.id if session.get(InternalUser, access.user.id) else None,
                    old_context=old_identity, new_context=identity_context(material),
                    reason="Category changed before linking a source folder."))
            return _commit_resource(session, material, actor.id, before)

    return router
