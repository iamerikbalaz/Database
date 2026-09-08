import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.config import get_settings
from app.db.models import Company, InternalUser, PBRMaterial, Project, PublishedBrand
from app.db.session import Database
from app.main import create_app


POSTGRES_TEST_ADMIN_URL = os.getenv("POSTGRES_TEST_ADMIN_URL")
if POSTGRES_TEST_ADMIN_URL is None and os.getenv("RUN_POSTGRES_TESTS") == "1":
    POSTGRES_TEST_ADMIN_URL = make_url(Settings().resolved_database_url).set(
        database="postgres"
    ).render_as_string(hide_password=False)
pytestmark = pytest.mark.skipif(
    POSTGRES_TEST_ADMIN_URL is None,
    reason="POSTGRES_TEST_ADMIN_URL is required for PostgreSQL integration tests",
)


@contextmanager
def isolated_postgresql_database():
    assert POSTGRES_TEST_ADMIN_URL is not None
    admin_url = make_url(POSTGRES_TEST_ADMIN_URL)
    database_name = f"reawote_material_test_{uuid4().hex}"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    database_url = admin_url.set(database=database_name)
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        yield database_url.render_as_string(hide_password=False)
    finally:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        admin_engine.dispose()


@pytest.fixture(scope="module")
def migrated_postgresql_url() -> str:
    with isolated_postgresql_database() as database_url:
        previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url
        get_settings.cache_clear()
        try:
            config = Config("alembic.ini")
            command.upgrade(config, "head")
            yield database_url
        finally:
            if previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_database_url
            get_settings.cache_clear()


def test_postgresql_alembic_upgrade_and_check(migrated_postgresql_url: str) -> None:
    config = Config("alembic.ini")

    command.check(config)


def test_concurrent_material_creation_allocates_distinct_numbers(
    migrated_postgresql_url: str,
) -> None:
    setup_engine = create_engine(migrated_postgresql_url)
    suffix = uuid4().hex
    with Session(setup_engine) as session:
        company = Company(name=f"Concurrency {suffix}")
        session.add(company)
        session.flush()
        project = Project(
            company_id=company.id,
            project_number=f"CONCURRENT-{suffix}",
            name="Concurrent allocation",
        )
        brand = PublishedBrand(
            company_id=company.id,
            name="Concurrent brand",
            folder_prefix=f"CONCURRENT{suffix.upper()}",
            brand_identifier=f"concurrent-{suffix}",
        )
        processor = InternalUser(
            display_name="Concurrent processor",
            email=f"concurrent-{suffix}@example.com",
            role="PROCESSOR",
        )
        session.add_all([project, brand, processor])
        session.commit()
        project_id = project.id
        brand_id = brand.id
        processor_id = processor.id

    barrier = Barrier(2)

    def create_one(index: int) -> tuple[int, dict[str, object]]:
        database = Database(migrated_postgresql_url)
        application = create_app(Settings(database_url=migrated_postgresql_url), database)
        with TestClient(application) as client:
            barrier.wait(timeout=10)
            response = client.post(
                "/api/materials",
                json={
                    "project_id": str(project_id),
                    "published_brand_id": str(brand_id),
                    "material_name": f"Concurrent material {index}",
                    "main_category_code": "G03",
                    "assigned_processor_id": str(processor_id),
                },
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create_one, (1, 2)))

    assert [status_code for status_code, _ in results] == [201, 201]
    materials = [body for _, body in results]
    assert {material["sequence_number"] for material in materials} == {1, 2}
    assert len({material["technical_identity"] for material in materials}) == 2

    with Session(setup_engine) as session:
        stored_brand = session.get(PublishedBrand, brand_id)
        assert stored_brand is not None
        assert stored_brand.next_sequence_number == 3
        stored_numbers = set(
            session.scalars(
                select(PBRMaterial.sequence_number).where(
                    PBRMaterial.published_brand_id == brand_id
                )
            )
        )
        assert stored_numbers == {1, 2}
    setup_engine.dispose()


def test_sequence_9999_is_allocated_then_returns_controlled_conflict(
    migrated_postgresql_url: str,
) -> None:
    setup_engine = create_engine(migrated_postgresql_url)
    suffix = uuid4().hex
    with Session(setup_engine) as session:
        company = Company(name=f"Boundary {suffix}")
        session.add(company)
        session.flush()
        project = Project(
            company_id=company.id,
            project_number=f"BOUNDARY-{suffix}",
            name="Boundary allocation",
        )
        brand = PublishedBrand(
            company_id=company.id,
            name="Boundary brand",
            folder_prefix=f"BOUNDARY{suffix.upper()}",
            brand_identifier=f"boundary-{suffix}",
            next_sequence_number=9999,
        )
        processor = InternalUser(
            display_name="Boundary processor",
            email=f"boundary-{suffix}@example.com",
            role="PROCESSOR",
        )
        session.add_all([project, brand, processor])
        session.commit()
        project_id = project.id
        brand_id = brand.id
        processor_id = processor.id

    database = Database(migrated_postgresql_url)
    application = create_app(Settings(database_url=migrated_postgresql_url), database)
    payload = {
        "project_id": str(project_id),
        "published_brand_id": str(brand_id),
        "material_name": "Final sequence material",
        "main_category_code": "G03",
        "assigned_processor_id": str(processor_id),
    }
    with TestClient(application) as client:
        created_response = client.post("/api/materials", json=payload)
        brand_response = client.get(f"/api/brands/{brand_id}")
        exhausted_response = client.post("/api/materials", json=payload)

    assert created_response.status_code == 201
    assert created_response.json()["sequence_number"] == 9999
    assert created_response.json()["technical_identity"].endswith("_9999_G03")
    assert brand_response.status_code == 200
    assert brand_response.json()["next_sequence_number"] == 10000
    assert exhausted_response.status_code == 409
    assert exhausted_response.json()["detail"] == "Published brand sequence is exhausted."

    with Session(setup_engine) as session:
        stored_numbers = list(
            session.scalars(
                select(PBRMaterial.sequence_number).where(
                    PBRMaterial.published_brand_id == brand_id
                )
            )
        )
        assert stored_numbers == [9999]
    setup_engine.dispose()
