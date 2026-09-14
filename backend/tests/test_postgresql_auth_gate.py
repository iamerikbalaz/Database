"""Exercise the PostgreSQL gate through actual pytest process exit statuses."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("auth_source", "extra_args", "expected_exit", "expected_counts"),
    [
        (
            "def test_one(): pass\ndef test_two(): pass\n",
            [],
            0,
            "collected=2, executed=2, passed=2, skipped=0, failed=0",
        ),
        (
            "def test_one(): assert False\n",
            [],
            1,
            "collected=1, executed=1, passed=0, skipped=0, failed=1",
        ),
        (
            "import pytest\n@pytest.mark.skip(reason='database absent')\ndef test_one(): pass\n",
            [],
            1,
            "collected=1, executed=0, passed=0, skipped=1, failed=0",
        ),
        (
            "import pytest\ndef test_one(): pass\n"
            "def test_two(): pytest.skip('unexpected runtime skip')\n",
            [],
            1,
            "collected=2, executed=2, passed=1, skipped=1, failed=0",
        ),
        (
            "import pytest\npytest.skip('module unavailable', allow_module_level=True)\n",
            [],
            1,
            "collected=0, executed=0, passed=0, skipped=1, failed=0",
        ),
        (
            "import pytest\n@pytest.fixture\ndef broken(): raise RuntimeError('setup')\n"
            "def test_one(broken): pass\n",
            [],
            1,
            "collected=1, executed=0, passed=0, skipped=0, failed=1",
        ),
        (
            "import pytest\n@pytest.fixture\ndef broken():\n yield\n raise RuntimeError('teardown')\n"
            "def test_one(broken): pass\n",
            [],
            1,
            "collected=1, executed=1, passed=0, skipped=0, failed=1",
        ),
        (
            "def test_one(): pass\n",
            ["-k", "material"],
            1,
            "collected=0, executed=0, passed=0, skipped=0, failed=0",
        ),
    ],
    ids=["pass", "fail", "skip", "partial-skip", "module-skip", "setup", "teardown", "none"],
)
def test_required_auth_postgresql_gate(
    tmp_path: Path,
    auth_source: str,
    extra_args: list[str],
    expected_exit: int,
    expected_counts: str,
) -> None:
    (tmp_path / "conftest.py").write_text(
        Path(__file__).with_name("conftest.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "test_auth_postgresql.py").write_text(auth_source, encoding="utf-8")
    (tmp_path / "test_materials_postgresql.py").write_text(
        "def test_material(): pass\n", encoding="utf-8"
    )
    environment = os.environ.copy()
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--confcutdir=.",
            "--require-auth-postgresql",
            "-q",
            "test_auth_postgresql.py",
            "test_materials_postgresql.py",
            *extra_args,
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == expected_exit, output
    assert f"Auth PostgreSQL gate: {expected_counts}" in output, output

