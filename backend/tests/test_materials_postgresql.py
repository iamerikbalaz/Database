import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
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


def _setup_prefix_race(migrated_postgresql_url: str) -> dict[str, object]:
    setup_engine = create_engine(migrated_postgresql_url)
    suffix = uuid4().hex[:12]
    old_prefix = f"OLD{suffix.upper()}"
    new_prefix = f"NEW{suffix.upper()}"
    with Session(setup_engine) as session:
        company = Company(name=f"Prefix race {suffix}")
        session.add(company)
        session.flush()
        project = Project(
            company_id=company.id,
            project_number=f"PREFIX-{suffix}",
            name="Prefix race",
        )
        brand = PublishedBrand(
            company_id=company.id,
            name="Prefix race brand",
            folder_prefix=old_prefix,
            brand_identifier=f"prefix-race-{suffix}",
        )
        processor = InternalUser(
            display_name="Prefix race processor",
            email=f"prefix-race-{suffix}@example.com",
            role="PROCESSOR",
        )
        session.add_all([project, brand, processor])
        session.commit()
        result: dict[str, object] = {
            "project_id": project.id,
            "brand_id": brand.id,
            "processor_id": processor.id,
            "old_prefix": old_prefix,
            "new_prefix": new_prefix,
        }
    setup_engine.dispose()
    return result


def _request_holding_brand_lock(
    database_url: str,
    method: str,
    path: str,
    payload: dict[str, object],
    lock_acquired: Event,
    release_lock: Event,
) -> tuple[int, dict[str, object]]:
    database = Database(database_url)

    def hold_after_lock(*args: object) -> None:
        statement = str(args[2])
        if "FOR UPDATE" in statement.upper():
            lock_acquired.set()
            if not release_lock.wait(timeout=10):
                raise TimeoutError("Timed out while holding the PublishedBrand row lock.")

    event.listen(database.engine, "after_cursor_execute", hold_after_lock)
    try:
        application = create_app(Settings(database_url=database_url), database)
        with TestClient(application) as client:
            response = client.request(method, path, json=payload)
            return response.status_code, response.json()
    finally:
        database.dispose()


def _request_waiting_for_row_lock(
    database_url: str,
    method: str,
    path: str,
    payload: dict[str, object],
    lock_attempted: Event,
    lock_acquired: Event,
) -> tuple[int, dict[str, object]]:
    database = Database(database_url)

    def record_lock_attempt(*args: object) -> None:
        statement = str(args[2])
        if "FOR UPDATE" in statement.upper():
            lock_attempted.set()

    def record_lock_acquisition(*args: object) -> None:
        statement = str(args[2])
        if "FOR UPDATE" in statement.upper():
            lock_acquired.set()

    event.listen(database.engine, "before_cursor_execute", record_lock_attempt)
    event.listen(database.engine, "after_cursor_execute", record_lock_acquisition)
    try:
        application = create_app(Settings(database_url=database_url), database)
        with TestClient(application) as client:
            response = client.request(method, path, json=payload)
            return response.status_code, response.json()
    finally:
        database.dispose()


def _assert_prefix_race_database_state(
    database_url: str,
    context: dict[str, object],
    expected_prefix: object,
) -> None:
    setup_engine = create_engine(database_url)
    with Session(setup_engine) as session:
        stored_brand = session.get(PublishedBrand, context["brand_id"])
        assert stored_brand is not None
        assert stored_brand.folder_prefix == expected_prefix
        materials = list(
            session.scalars(
                select(PBRMaterial).where(
                    PBRMaterial.published_brand_id == context["brand_id"]
                )
            )
        )
        assert len(materials) == 1
        assert materials[0].sequence_number == 1
        assert materials[0].technical_identity == f"{expected_prefix}_0001_G03"
    setup_engine.dispose()


def test_prefix_patch_first_serializes_material_creation(
    migrated_postgresql_url: str,
) -> None:
    context = _setup_prefix_race(migrated_postgresql_url)
    brand_path = f"/api/brands/{context['brand_id']}"
    material_payload = {
        "project_id": str(context["project_id"]),
        "published_brand_id": str(context["brand_id"]),
        "material_name": "Material after prefix update",
        "main_category_code": "G03",
        "assigned_processor_id": str(context["processor_id"]),
    }
    first_locked = Event()
    release_first = Event()
    second_attempted = Event()
    second_acquired = Event()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            _request_holding_brand_lock,
            migrated_postgresql_url,
            "PATCH",
            brand_path,
            {"folder_prefix": context["new_prefix"]},
            first_locked,
            release_first,
        )
        try:
            assert first_locked.wait(timeout=10)
            second_future = executor.submit(
                _request_waiting_for_row_lock,
                migrated_postgresql_url,
                "POST",
                "/api/materials",
                material_payload,
                second_attempted,
                second_acquired,
            )
            assert second_attempted.wait(timeout=10)
            assert not second_acquired.wait(timeout=0.25)
        finally:
            release_first.set()
        patch_result = first_future.result(timeout=10)
        post_result = second_future.result(timeout=10)

    assert patch_result[0] == 200
    assert patch_result[1]["folder_prefix"] == context["new_prefix"]
    assert post_result[0] == 201
    assert post_result[1]["technical_identity"] == (
        f"{context['new_prefix']}_0001_G03"
    )
    _assert_prefix_race_database_state(
        migrated_postgresql_url,
        context,
        context["new_prefix"],
    )


def test_material_creation_first_blocks_prefix_patch_and_returns_conflict(
    migrated_postgresql_url: str,
) -> None:
    context = _setup_prefix_race(migrated_postgresql_url)
    brand_path = f"/api/brands/{context['brand_id']}"
    material_payload = {
        "project_id": str(context["project_id"]),
        "published_brand_id": str(context["brand_id"]),
        "material_name": "Material before prefix update",
        "main_category_code": "G03",
        "assigned_processor_id": str(context["processor_id"]),
    }
    first_locked = Event()
    release_first = Event()
    second_attempted = Event()
    second_acquired = Event()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            _request_holding_brand_lock,
            migrated_postgresql_url,
            "POST",
            "/api/materials",
            material_payload,
            first_locked,
            release_first,
        )
        try:
            assert first_locked.wait(timeout=10)
            second_future = executor.submit(
                _request_waiting_for_row_lock,
                migrated_postgresql_url,
                "PATCH",
                brand_path,
                {"folder_prefix": context["new_prefix"]},
                second_attempted,
                second_acquired,
            )
            assert second_attempted.wait(timeout=10)
            assert not second_acquired.wait(timeout=0.25)
        finally:
            release_first.set()
        post_result = first_future.result(timeout=10)
        patch_result = second_future.result(timeout=10)

    assert post_result[0] == 201
    assert post_result[1]["technical_identity"] == (
        f"{context['old_prefix']}_0001_G03"
    )
    assert patch_result == (
        409,
        {"detail": "folder_prefix cannot be changed after materials have been created."},
    )
    _assert_prefix_race_database_state(
        migrated_postgresql_url,
        context,
        context["old_prefix"],
    )


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


def _setup_material_path_race(migrated_postgresql_url: str) -> dict[str, object]:
    setup_engine = create_engine(migrated_postgresql_url)
    suffix = uuid4().hex
    prefix = f"PATHRACE{suffix.upper()}"
    with Session(setup_engine) as session:
        company = Company(name=f"Material path race {suffix}")
        session.add(company)
        session.flush()
        project = Project(
            company_id=company.id,
            project_number=f"PATH-RACE-{suffix}",
            name="Material path race",
        )
        brand = PublishedBrand(
            company_id=company.id,
            name="Material path race brand",
            folder_prefix=prefix,
            brand_identifier=f"material-path-race-{suffix}",
            next_sequence_number=2,
        )
        processor = InternalUser(
            display_name="Material path race processor",
            email=f"material-path-race-{suffix}@example.com",
            role="PROCESSOR",
        )
        session.add_all([project, brand, processor])
        session.flush()
        material = PBRMaterial(
            project_id=project.id,
            published_brand_id=brand.id,
            sequence_number=1,
            material_name="Material path race",
            main_category_code="G03",
            assigned_processor_id=processor.id,
            technical_identity=f"{prefix}_0001_G03",
        )
        session.add(material)
        session.commit()
        context: dict[str, object] = {
            "material_id": material.id,
            "technical_identity": material.technical_identity,
            "folder_path": f"materials/{prefix}_0001_G03",
        }
    setup_engine.dispose()
    return context


def _simulate_future_system_link_transaction(
    database_url: str,
    material_id: object,
    folder_path: str,
    lock_acquired: Event,
    release_lock: Event,
) -> None:
    """Simulate the future filesystem-verified link operation at the DB boundary."""
    link_engine = create_engine(database_url)
    try:
        with Session(link_engine) as session:
            material = session.scalar(
                select(PBRMaterial)
                .where(PBRMaterial.id == material_id)
                .with_for_update()
            )
            assert material is not None
            material.folder_path = folder_path
            session.flush()
            lock_acquired.set()
            if not release_lock.wait(timeout=10):
                raise TimeoutError("Timed out while holding the PBRMaterial row lock.")
            session.commit()
    finally:
        link_engine.dispose()


def test_concurrent_system_link_and_category_patch_use_locked_current_state(
    migrated_postgresql_url: str,
) -> None:
    context = _setup_material_path_race(migrated_postgresql_url)
    material_path = f"/api/materials/{context['material_id']}"
    link_locked = Event()
    release_link = Event()
    patch_attempted = Event()
    patch_acquired = Event()

    with ThreadPoolExecutor(max_workers=2) as executor:
        link_future = executor.submit(
            _simulate_future_system_link_transaction,
            migrated_postgresql_url,
            context["material_id"],
            context["folder_path"],
            link_locked,
            release_link,
        )
        try:
            assert link_locked.wait(timeout=10)
            patch_future = executor.submit(
                _request_waiting_for_row_lock,
                migrated_postgresql_url,
                "PATCH",
                material_path,
                {"main_category_code": "G04"},
                patch_attempted,
                patch_acquired,
            )
            assert patch_attempted.wait(timeout=10)
            assert not patch_acquired.wait(timeout=0.25)
        finally:
            release_link.set()
        link_future.result(timeout=10)
        patch_result = patch_future.result(timeout=10)

    assert patch_acquired.is_set()
    assert patch_result == (
        409,
        {"detail": "main_category_code cannot be changed while folder_path is set."},
    )

    database = Database(migrated_postgresql_url)
    application = create_app(Settings(database_url=migrated_postgresql_url), database)
    try:
        with TestClient(application) as client:
            stored_response = client.get(material_path)
            follow_up_response = client.patch(
                material_path,
                json={"material_name": "Transaction remains usable"},
            )
    finally:
        database.dispose()

    assert stored_response.status_code == 200
    stored = stored_response.json()
    assert stored["main_category_code"] == "G03"
    assert stored["technical_identity"] == context["technical_identity"]
    assert stored["folder_path"] == context["folder_path"]
    assert not (
        stored["technical_identity"].endswith("_G04")
        and stored["folder_path"].endswith("_G03")
    )
    assert follow_up_response.status_code == 200


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
