from contextlib import AbstractContextManager
from typing import Annotated, Any, Protocol, TypeVar
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Company,
    InternalUser,
    MaterialPublicationStatus,
    MaterialValidationStatus,
    MaterialWorkflowStatus,
    PBRMaterial,
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


def build_resources_router(database: SessionDatabase) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/companies", response_model=list[CompanyRead], tags=["companies"])
    def list_companies(filters: Annotated[CompanyListFilters, Query()]) -> list[Company]:
        with database.session() as session:
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
    def create_company(payload: CompanyCreate) -> Company:
        with database.session() as session:
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
            return _commit(session, company)

    @router.get("/companies/{company_id}", response_model=CompanyRead, tags=["companies"])
    def get_company(company_id: UUID) -> Company:
        with database.session() as session:
            return _get_or_404(session, Company, company_id, "Company")

    @router.patch("/companies/{company_id}", response_model=CompanyRead, tags=["companies"])
    def update_company(company_id: UUID, payload: CompanyUpdate) -> Company:
        with database.session() as session:
            company = _get_or_404(session, Company, company_id, "Company")
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
            return _commit(session, company)

    @router.get("/brands", response_model=list[PublishedBrandRead], tags=["brands"])
    def list_brands(
        filters: Annotated[PublishedBrandListFilters, Query()],
    ) -> list[PublishedBrand]:
        with database.session() as session:
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
    def create_brand(payload: PublishedBrandCreate) -> PublishedBrand:
        with database.session() as session:
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
            return _commit(session, brand)

    @router.get("/brands/{brand_id}", response_model=PublishedBrandRead, tags=["brands"])
    def get_brand(brand_id: UUID) -> PublishedBrand:
        with database.session() as session:
            return _get_or_404(session, PublishedBrand, brand_id, "Published brand")

    @router.patch("/brands/{brand_id}", response_model=PublishedBrandRead, tags=["brands"])
    def update_brand(brand_id: UUID, payload: PublishedBrandUpdate) -> PublishedBrand:
        with database.session() as session:
            values = _values(payload, exclude_unset=True)
            if "folder_prefix" in values:
                brand = session.scalar(
                    select(PublishedBrand)
                    .where(PublishedBrand.id == brand_id)
                    .with_for_update()
                )
                if brand is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Published brand not found.",
                    )
            else:
                brand = _get_or_404(session, PublishedBrand, brand_id, "Published brand")
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
            _apply_update(brand, values)
            return _commit(session, brand)

    @router.get("/projects", response_model=list[ProjectRead], tags=["projects"])
    def list_projects(filters: Annotated[ProjectListFilters, Query()]) -> list[Project]:
        with database.session() as session:
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
    def create_project(payload: ProjectCreate) -> Project:
        with database.session() as session:
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
            return _commit(session, project)

    @router.get("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
    def get_project(project_id: UUID) -> Project:
        with database.session() as session:
            return _get_or_404(session, Project, project_id, "Project")

    @router.patch("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
    def update_project(project_id: UUID, payload: ProjectUpdate) -> Project:
        with database.session() as session:
            project = _get_or_404(session, Project, project_id, "Project")
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
            return _commit(session, project)

    @router.get(
        "/internal-users",
        response_model=list[InternalUserRead],
        tags=["internal-users"],
    )
    def list_internal_users(
        filters: Annotated[InternalUserListFilters, Query()],
    ) -> list[InternalUser]:
        with database.session() as session:
            statement = select(InternalUser)
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
    def create_internal_user(payload: InternalUserCreate) -> InternalUser:
        with database.session() as session:
            _ensure_unique(
                session,
                InternalUser,
                InternalUser.email,
                payload.email,
                "email",
            )
            user = InternalUser(**_values(payload))
            session.add(user)
            return _commit(session, user)

    @router.get(
        "/internal-users/{user_id}",
        response_model=InternalUserRead,
        tags=["internal-users"],
    )
    def get_internal_user(user_id: UUID) -> InternalUser:
        with database.session() as session:
            return _get_or_404(session, InternalUser, user_id, "Internal user")

    @router.patch(
        "/internal-users/{user_id}",
        response_model=InternalUserRead,
        tags=["internal-users"],
    )
    def update_internal_user(user_id: UUID, payload: InternalUserUpdate) -> InternalUser:
        with database.session() as session:
            user = _get_or_404(session, InternalUser, user_id, "Internal user")
            values = _values(payload, exclude_unset=True)
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
            return _commit(session, user)

    @router.get("/materials", response_model=list[PBRMaterialRead], tags=["materials"])
    def list_materials(
        filters: Annotated[PBRMaterialListFilters, Query()],
    ) -> list[PBRMaterial]:
        with database.session() as session:
            statement = select(PBRMaterial)
            for field_name in (
                "project_id",
                "published_brand_id",
                "workflow_status",
                "validation_status",
                "publication_status",
                "is_published",
            ):
                value = getattr(filters, field_name)
                if value is not None:
                    statement = statement.where(getattr(PBRMaterial, field_name) == value)
            return list(
                session.scalars(statement.order_by(PBRMaterial.created_at, PBRMaterial.id))
            )

    @router.post(
        "/materials",
        response_model=PBRMaterialRead,
        status_code=status.HTTP_201_CREATED,
        tags=["materials"],
    )
    def create_material(payload: PBRMaterialCreate) -> PBRMaterial:
        with database.session() as session:
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
                workflow_status=MaterialWorkflowStatus.IN_PROGRESS.value,
                validation_status=MaterialValidationStatus.NOT_CHECKED.value,
                is_published=False,
                publication_status=MaterialPublicationStatus.NOT_PUBLISHED.value,
            )
            brand.next_sequence_number = sequence_number + 1
            session.add(material)
            return _commit(session, material)

    @router.get("/materials/{material_id}", response_model=PBRMaterialRead, tags=["materials"])
    def get_material(material_id: UUID) -> PBRMaterial:
        with database.session() as session:
            return _get_or_404(session, PBRMaterial, material_id, "PBR material")

    @router.patch(
        "/materials/{material_id}",
        response_model=PBRMaterialRead,
        tags=["materials"],
    )
    def update_material(material_id: UUID, payload: PBRMaterialUpdate) -> PBRMaterial:
        with database.session() as session:
            material = _get_or_404(session, PBRMaterial, material_id, "PBR material")
            values = _values(payload, exclude_unset=True)
            original_folder_path = material.folder_path
            if "project_id" in values:
                _get_or_404(session, Project, values["project_id"], "Project")
            if "assigned_processor_id" in values:
                _require_active_internal_user(session, values["assigned_processor_id"])
            if (
                "main_category_code" in values
                and values["main_category_code"] != material.main_category_code
            ):
                if original_folder_path is not None:
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
            if (
                "folder_path" in values
                and original_folder_path is not None
                and values["folder_path"] != original_folder_path
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="folder_path cannot be changed after it has been set.",
                )
            _apply_update(material, values)
            return _commit(session, material)

    return router
