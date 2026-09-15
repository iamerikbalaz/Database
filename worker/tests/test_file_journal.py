import os
from uuid import uuid4

import pytest

from app.file_journal import JournalError, open_journal, rename_noreplace
from app.secure_filesystem import _directory_flags, secure_filesystem_access_supported

POSIX = pytest.mark.skipif(not secure_filesystem_access_supported(), reason="Linux durable filesystem journal")


@POSIX
def test_state_is_private_durable_and_owned_by_one_operation(tmp_path):
    key = str(uuid4())
    with open_journal(tmp_path, key) as journal:
        assert journal.read() is None
        journal.write({"schema_version": 1, "state": "PREPARED", "next_step": 0})
        with pytest.raises(JournalError, match="JOURNAL_BUSY"):
            with open_journal(tmp_path, key): pytest.fail("must not acquire twice")
    with open_journal(tmp_path, key) as journal:
        assert journal.read()["state"] == "PREPARED"
        journal.write({"schema_version": 1, "state": "COMPLETED", "next_step": 3})
    assert (tmp_path / key).stat().st_mode & 0o777 == 0o700
    assert (tmp_path / key / "state.json").stat().st_mode & 0o777 == 0o600
    with open_journal(tmp_path, key) as journal: assert journal.read()["state"] == "COMPLETED"


@POSIX
def test_interrupted_atomic_state_update_keeps_previous_checkpoint(tmp_path, monkeypatch):
    key = str(uuid4())
    with open_journal(tmp_path, key) as journal:
        journal.write({"step": 1})
        replace = os.replace
        def fail(*args, **kwargs): raise OSError("SYNTHETIC_PRIVATE_ERROR")
        monkeypatch.setattr(os, "replace", fail)
        with pytest.raises(JournalError, match="JOURNAL_WRITE_FAILED") as caught:
            journal.write({"step": 2})
        assert "PRIVATE" not in str(caught.value)
        assert journal.read() == {"step": 1}
        monkeypatch.setattr(os, "replace", replace)
        journal.write({"step": 3})
        assert journal.read() == {"step": 3}


@POSIX
@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_pending_state_never_truncates_linked_outside_file(tmp_path, link):
    root = tmp_path / "journals"; root.mkdir(mode=0o700)
    protected = tmp_path / "protected"; protected.write_bytes(b"must survive"); protected.chmod(0o600)
    key = str(uuid4())
    with open_journal(root, key) as journal:
        path = root / key / "state.pending"
        if link == "symlink": path.symlink_to(protected)
        else: os.link(protected, path)
        with pytest.raises(JournalError): journal.write({"state": "PREPARED"})
    assert protected.read_bytes() == b"must survive"


@POSIX
def test_linked_or_corrupt_journal_cannot_be_read_as_success(tmp_path):
    key = str(uuid4())
    with open_journal(tmp_path, key) as journal:
        journal.write({"state": "PREPARED"})
        path = tmp_path / key / "state.json"
        path.write_bytes(b"PRIVATE_INVALID_JSON")
        with pytest.raises(JournalError, match="JOURNAL_INVALID_STATE") as caught: journal.read()
        assert "PRIVATE" not in str(caught.value)
        path.unlink(); path.symlink_to(tmp_path / "missing")
        with pytest.raises(JournalError, match="JOURNAL_UNSAFE_STORAGE"): journal.read()


@POSIX
def test_atomic_rename_refuses_existing_target_and_preserves_file_inode(tmp_path):
    source = tmp_path / "source"; source.write_bytes(b"source")
    target = tmp_path / "target"; target.write_bytes(b"target")
    inode = source.stat().st_ino
    fd = os.open(tmp_path, _directory_flags())
    try:
        with pytest.raises(JournalError, match="JOURNAL_TARGET_EXISTS"):
            rename_noreplace(fd, "source", fd, "target")
        assert source.read_bytes() == b"source" and target.read_bytes() == b"target"
        rename_noreplace(fd, "source", fd, "renamed")
        assert not source.exists() and (tmp_path / "renamed").stat().st_ino == inode
    finally: os.close(fd)


@POSIX
def test_world_writable_journal_root_is_rejected(tmp_path):
    tmp_path.chmod(0o777)
    try:
        with pytest.raises(JournalError, match="JOURNAL_UNSAFE_STORAGE"):
            with open_journal(tmp_path, str(uuid4())): pytest.fail("unsafe storage")
    finally: tmp_path.chmod(0o700)


@pytest.mark.parametrize("key", ["../../outside", "not-a-uuid", "00000000-0000-1000-8000-000000000000"])
def test_operation_ids_cannot_escape_the_journal_root(tmp_path, key):
    with pytest.raises(JournalError, match="JOURNAL_ID_INVALID"):
        with open_journal(tmp_path, key): pytest.fail("invalid operation id")


@pytest.mark.parametrize("name", ["../outside", "/outside", "C:\\outside", ".", "bad\x00name"])
def test_rename_requires_single_safe_components_before_syscall(name):
    with pytest.raises(JournalError, match="JOURNAL_NAME_INVALID"):
        rename_noreplace(-1, name, -1, "target")
