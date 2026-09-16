from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_has_one_expected_head() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))

    assert scripts.get_heads() == ["20260916_0010"]
