"""Optional fail-closed gate for the complete PostgreSQL test phase."""

import pytest
from _pytest.terminal import TerminalReporter


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-auth-postgresql",
        action="store_true",
        help="Fail unless every collected auth PostgreSQL test runs and passes.",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--require-auth-postgresql"):
        config.pluginmanager.register(_AuthPostgresqlGate(), "auth-postgresql-gate")


def _is_auth_postgresql(nodeid: str) -> bool:
    return (
        nodeid.split("::", 1)[0].replace("\\", "/").rsplit("/", 1)[-1]
        == "test_auth_postgresql.py"
    )


class _AuthPostgresqlGate:
    def __init__(self) -> None:
        self.collected: set[str] = set()
        self.executed: set[str] = set()
        self.passed: set[str] = set()
        self.skipped: set[str] = set()
        self.failed: set[str] = set()

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collected = {
            item.nodeid for item in session.items if _is_auth_postgresql(item.nodeid)
        }

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if _is_auth_postgresql(report.nodeid):
            if report.skipped:
                self.skipped.add(report.nodeid)
            elif report.failed:
                self.failed.add(report.nodeid)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if not _is_auth_postgresql(report.nodeid):
            return
        if report.when == "call":
            self.executed.add(report.nodeid)
            if report.passed:
                self.passed.add(report.nodeid)
        if report.skipped:
            self.skipped.add(report.nodeid)
        elif report.failed:
            self.failed.add(report.nodeid)

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if (
            not self.collected
            or self.executed != self.collected
            or self.passed != self.collected
            or self.skipped
            or self.failed
        ) and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter: TerminalReporter) -> None:
        successful = self.passed - self.skipped - self.failed
        terminalreporter.write_sep(
            "=",
            "Auth PostgreSQL gate: "
            f"collected={len(self.collected)}, executed={len(self.executed)}, "
            f"passed={len(successful)}, skipped={len(self.skipped)}, "
            f"failed={len(self.failed)}",
        )
