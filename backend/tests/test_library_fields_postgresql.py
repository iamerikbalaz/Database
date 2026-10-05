"""The library-field command works with migrated PostgreSQL audit guards."""
from types import SimpleNamespace

import pytest

from test_library_fields import test_append_tags_and_credits_preserve_all_other_content_and_replay as exercise
from test_materials_postgresql import review_pg_case, migrated_postgresql_url, POSTGRES_TEST_ADMIN_URL  # noqa: F401

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None,
    reason="An isolated PostgreSQL database is required")]


def test_postgresql_library_fields_preserve_content_and_exact_receipts(review_pg_case):
    case = review_pg_case
    adapter = SimpleNamespace(database=case.database, materials=[case.material], client=lambda _: case.client_for())
    exercise(adapter)
