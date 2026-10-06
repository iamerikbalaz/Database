"""Customer AI adoption uses real migrated immutable directory receipts."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.db.notion_sync_models import NotionSyncState
from test_customer_ai_brief import apply_payload, brief, counts, exercise_customer_ai, results_for
from test_customer_orders import create_customer
from test_materials_postgresql import POSTGRES_TEST_ADMIN_URL, isolated_postgresql_database, migrated_postgresql_url, review_pg_case  # noqa: F401

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason='isolated PostgreSQL required')


def test_postgresql_customer_ai_adoption_preserves_fields_and_replays_durably(review_pg_case):
    with review_pg_case.client_for() as client:
        exercise_customer_ai(review_pg_case.database, client)


@pytest.mark.parametrize('same_key', [True, False])
def test_postgresql_concurrent_customer_ai_adoption_creates_one_change_and_receipt(review_pg_case, same_key):
    case = review_pg_case
    with case.client_for() as client:
        customer = create_customer(client, name='Concurrent AI ' + uuid4().hex, brand_identifier='AI-' + uuid4().hex)
        results = results_for(brief(client, [customer['id']]))
    first_payload = apply_payload(results)
    second_payload = first_payload if same_key else {**first_payload, 'idempotency_key': str(uuid4())}
    baseline = counts(case.database)
    barrier = Barrier(2)
    def apply(client, payload):
        barrier.wait(timeout=15)
        return client.post('/api/customers/' + customer['id'] + '/ai-brief-result', json=payload)
    with case.client_for() as first, case.client_for(0 if same_key else 3) as second:
        with ThreadPoolExecutor(2) as pool:
            first_future = pool.submit(apply, first, first_payload)
            second_future = pool.submit(apply, second, second_payload)
            responses = [first_future.result(timeout=30), second_future.result(timeout=30)]
    assert [item.status_code for item in responses] == [200, 200], [item.text for item in responses]
    assert responses[0].json() == responses[1].json()
    assert counts(case.database) == [baseline[0] + 1, baseline[1] + 1]
    with case.database.session() as session:
        sync = session.scalar(select(NotionSyncState).where(NotionSyncState.entity_id == UUID(customer['id'])))
        assert sync.revision == 2 and sync.status == 'PENDING'
