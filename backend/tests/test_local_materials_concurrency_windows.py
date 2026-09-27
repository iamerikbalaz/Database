"""Real Windows sharing/reparse boundaries in disposable owned directories."""
import os
from uuid import uuid4

import pytest

from app.identity_client import IdentityClientError
from app.local_filesystem import LocalFilesError
from app.metadata_client import MetadataClientError
from test_local_materials_windows import FOLDER, library, request, target

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows filesystem adapter")


def test_metadata_reader_blocks_replace_and_exact_request_recovers_after_close(library):
    library.metadata.execute(request())
    current = library.metadata.inspect(FOLDER)
    command = request(expected_sha256=current.sha256,
        values={"hex_color": "#FFFFFF", "width_cm": "3.1", "height_cm": "4.2"})
    path = library.fs.path(FOLDER) / "metadata.json"
    before = path.read_bytes()
    with library.fs.opened(path) as reader:
        with pytest.raises(MetadataClientError):
            library.metadata.execute(command)
        assert library.fs.read_handle(reader) == before
        assert path.read_bytes() == before
    assert library.metadata.execute(command).status == "COMPLETED"
    assert library.metadata.inspect(FOLDER).width_cm == "3.1"


def test_verified_reader_prevents_external_truncate_delete_and_replace(library):
    path = library.fs.path(FOLDER) / "4K/BRAND_0001_OLD_COL_4K.png"
    replacement = path.with_name("replacement.png")
    replacement.write_bytes(b"replacement")
    before = path.read_bytes()
    with library.fs.opened(path) as reader:
        with pytest.raises(OSError): path.write_bytes(b"truncated")
        with pytest.raises(OSError): path.unlink()
        with pytest.raises(OSError): os.replace(replacement, path)
        assert library.fs.read_handle(reader) == before
    assert path.read_bytes() == before and replacement.read_bytes() == b"replacement"


def test_no_replace_rename_preserves_both_existing_files(library):
    source = library.fs.root / "source.txt"
    destination = library.fs.root / "destination.txt"
    source.write_bytes(b"source")
    destination.write_bytes(b"destination")
    with library.fs.directory(""), library.fs.opened(source, rename=True) as handle:
        with pytest.raises(LocalFilesError): library.fs.rename_handle(handle, destination)
    assert source.read_bytes() == b"source" and destination.read_bytes() == b"destination"


def test_target_occupied_after_identity_preview_rejects_without_source_changes(library):
    proposal = target()
    plan = library.identity.plan(proposal)
    assert plan.ready
    destination = library.fs.path(proposal["target_path"])
    destination.mkdir()
    (destination / "existing.txt").write_bytes(b"keep destination")
    result = library.identity.execute({**proposal, "operation_id": str(uuid4()), "expected_plan_hash": plan.plan_hash})
    assert result.status == "REJECTED"
    assert (library.fs.path(FOLDER) / "4K/BRAND_0001_OLD_COL_4K.png").read_bytes() == b"unaltered map content"
    assert (destination / "existing.txt").read_bytes() == b"keep destination"


def test_source_junction_is_rejected_before_external_content_is_read(library, tmp_path):
    import _winapi
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "metadata.json").write_bytes(b"PRIVATE_EXTERNAL_DOCUMENT")
    junction = library.fs.path(FOLDER) / "outside-link"
    _winapi.CreateJunction(str(outside), str(junction))
    with pytest.raises(LocalFilesError): library.fs.read(FOLDER + "/outside-link/metadata.json")
    with pytest.raises(IdentityClientError): library.identity.plan(target())
    listing = library.listing(FOLDER)
    assert listing.omitted_entries == 1
    assert all(item.name != "outside-link" for item in listing.entries)
    assert (outside / "metadata.json").read_bytes() == b"PRIVATE_EXTERNAL_DOCUMENT"


def test_pinned_source_ancestors_cannot_be_replaced_during_access(library):
    parent = library.fs.path("Brand")
    with library.fs.directory(FOLDER):
        with pytest.raises(OSError): parent.rename(library.fs.path("Replaced"))
    assert library.fs.path(FOLDER).is_dir()


def test_replaced_journal_root_creates_nothing_outside_verified_journal(library, tmp_path):
    import _winapi
    outside = tmp_path / "outside-journal"
    outside.mkdir()
    original = library.journal.root
    original.rename(tmp_path / "original-journal")
    _winapi.CreateJunction(str(outside), str(original))
    with pytest.raises(LocalFilesError): library.journal_state(str(uuid4()), {"synthetic": True})
    assert list(outside.iterdir()) == []
