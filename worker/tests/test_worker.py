import pytest

from app.main import WorkerSettings, run_once


def test_run_once_is_idle() -> None:
    assert run_once() == "idle"


def test_settings_are_loaded_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "2.5")

    assert WorkerSettings.from_environment().poll_interval_seconds == 2.5


def test_non_positive_interval_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "0")

    with pytest.raises(ValueError, match="greater than zero"):
        WorkerSettings.from_environment()
