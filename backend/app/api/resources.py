from contextlib import AbstractContextManager
from typing import Any, Protocol, TypeVar
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Company, Project, PublishedBrand
from app.schemas import (
    CompanyCreate,
    CompanyRead,
    CompanyUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    PublishedBrandCreate,
    PublishedBrandRead,
    PublishedBrandUpdate,
)


class SessionDatabase(Protocol):
    def session(self) -> AbstractContextManager[Session]: ...


ModelT = TypeVar("ModelT", Company, PublishedBrand, Project)


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
    def list_companies() -> list[Company]:
        with database.session() as session:
            return list(session.scalars(select(Company).order_by(Company.created_at, Company.id)))

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
    def list_brands() -> list[PublishedBrand]:
        with database.session() as session:
            return list(
                session.scalars(
                    select(PublishedBrand).order_by(PublishedBrand.created_at, PublishedBrand.id)
                )
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
            brand = _get_or_404(session, PublishedBrand, brand_id, "Published brand")
            values = _values(payload, exclude_unset=True)
            if "company_id" in values:
                _require_company(session, values["company_id"])
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
    def list_projects() -> list[Project]:
        with database.session() as session:
            return list(session.scalars(select(Project).order_by(Project.created_at, Project.id)))

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

    return router
