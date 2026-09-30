from pathlib import Path
from types import SimpleNamespace

import pytest

from app.order_folders import (OrderFolderError, apply_folder_operation, checked_root,
    enqueue_order_folder_create, folder_preflight, known_folder_numbers,
    process_next_folder_operation, recover_interrupted_folders, validate_folder_name)
from app.db.models import Project
from app.db.notion_sync_models import OrderFolderOperation
from app.db.directory_models import DirectoryChangeEvent
from sqlalchemy import select
from test_notion_outbound import sync_db  # noqa: F401

NAME = "0253_ACME BRAND_SCANNING_FABRICS_092026"


def test_create_preserves_exact_formula_spaces_and_never_adopts_existing(tmp_path):
    operation = SimpleNamespace(action="CREATE", target_name=NAME, source_path=None)
    created = apply_folder_operation(str(tmp_path), operation)
    assert Path(created).name == NAME and Path(created).is_dir()
    assert known_folder_numbers(str(tmp_path)) == {"0253"}
    with pytest.raises(OrderFolderError, match="COLLISION"):
        apply_folder_operation(str(tmp_path), operation)


def test_number_collision_is_rejected_even_for_different_generated_name(tmp_path):
    (tmp_path / "0253_OTHER_SCANNING_092026").mkdir()
    with pytest.raises(OrderFolderError, match="ORDER_NUMBER_FOLDER_CONFLICT"):
        folder_preflight(str(tmp_path), NAME)


@pytest.mark.parametrize("name", ["../escape", "0253_A/B_X_092026", "0253_A\\B_X_092026",
    "0253_A:B_X_092026", "0253_X_092026.", "253_X", "0253_", "0253_A\x00B_092026"])
def test_unsafe_names_are_rejected_without_sanitizing(name):
    with pytest.raises(OrderFolderError): validate_folder_name(name)


def test_rename_requires_original_identity_and_never_overwrites(tmp_path):
    old = tmp_path / NAME
    old.mkdir(); (old / "customer-file.txt").write_text("keep", encoding="utf-8")
    target = NAME.replace("FABRICS", "TILES")
    _, identity = folder_preflight(str(tmp_path), target, str(old))
    operation = SimpleNamespace(action="RENAME", target_name=target, source_path=str(old), source_identity="wrong")
    with pytest.raises(OrderFolderError, match="ORDER_FOLDER_CHANGED"):
        apply_folder_operation(str(tmp_path), operation)
    operation.source_identity = identity
    result = Path(apply_folder_operation(str(tmp_path), operation))
    assert not old.exists() and (result / "customer-file.txt").read_text() == "keep"


def test_outside_root_and_nested_source_are_rejected(tmp_path):
    nested = tmp_path / "sub" / NAME
    nested.mkdir(parents=True)
    with pytest.raises(OrderFolderError, match="OUTSIDE_ROOT"):
        folder_preflight(str(tmp_path), NAME.replace("FABRICS", "TILES"), str(nested))


def test_symlink_source_is_rejected(tmp_path):
    actual = tmp_path / "real"
    actual.mkdir()
    link = tmp_path / NAME
    try: link.symlink_to(actual, target_is_directory=True)
    except OSError: pytest.skip("Symlink permission is unavailable on this Windows host")
    with pytest.raises(OrderFolderError, match="LINK_FORBIDDEN"):
        folder_preflight(str(tmp_path), NAME.replace("FABRICS", "TILES"), str(link))


def test_durable_create_updates_order_and_audit_only_after_folder_creation(sync_db, tmp_path):
    db, actor, _, order = sync_db
    with db.session() as session:
        order = session.get(Project, order.id)
        operation = enqueue_order_folder_create(session, order, actor.id)
        session.commit()
    config = SimpleNamespace(order_folders_enabled=False, order_folders_root=str(tmp_path))
    assert not process_next_folder_operation(db, config) and not (tmp_path / order.name).exists()
    config.order_folders_enabled = True
    assert process_next_folder_operation(db, config)
    with db.session() as session:
        saved = session.get(Project, order.id)
        assert Path(saved.folder_path).is_dir()
        assert session.get(OrderFolderOperation, operation.id).status == "COMPLETED"
        assert session.scalar(select(DirectoryChangeEvent)).action == "FOLDER_CREATED"
    assert not process_next_folder_operation(db, config)


def test_stale_queued_create_never_writes_old_name(sync_db, tmp_path):
    db, actor, _, order = sync_db
    with db.session() as session:
        order = session.get(Project, order.id)
        operation = enqueue_order_folder_create(session, order, actor.id)
        session.commit()
        order.name = order.name.replace("FABRICS", "TILES")
        session.commit()
    process_next_folder_operation(db, SimpleNamespace(order_folders_enabled=True, order_folders_root=str(tmp_path)))
    with db.session() as session:
        saved = session.get(OrderFolderOperation, operation.id)
        assert saved.status == "ERROR" and saved.error_code == "ORDER_FOLDER_RECORD_CHANGED"
    assert not list(tmp_path.iterdir())


def test_interrupted_folder_create_is_held_for_reconciliation(sync_db):
    db, actor, _, order = sync_db
    with db.session() as session:
        operation = enqueue_order_folder_create(session, session.get(Project, order.id), actor.id)
        operation.status = "RUNNING"; session.commit()
    recover_interrupted_folders(db)
    with db.session() as session:
        assert session.get(OrderFolderOperation, operation.id).status == "RECONCILE"
