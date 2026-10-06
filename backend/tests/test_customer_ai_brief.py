"""Customer AI JSON uses authenticated, bounded, durable human approval."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.db.directory_models import DirectoryChangeEvent, DirectoryCommand
from app.db.models import PublishedBrand
from app.db.notion_sync_models import NotionSyncState
from app.main import create_app
from test_application_access import access_case  # noqa: F401
from test_customer_orders import create_customer, write


def brief(client, identifiers):
    selection = [{'id': identifier, 'expected_updated_at': client.get('/api/customers/' + identifier).json()['updated_at']} for identifier in identifiers]
    response = client.post('/api/customer-ai/brief', json={'selection': selection})
    assert response.status_code == 200, response.text
    return response.json()


def results_for(exported, **updates):
    results = deepcopy(exported['result_template'])
    for item in results['items']:
        item.update({'description': 'A manufacturer of verified surface materials.', 'website': 'https://official.example/',
            'source_urls': ['https://official.example/about'], 'needs_review': False, 'note': 'Verified official company identity.', **updates})
    return results


def review(client, results, identifiers=None):
    response = client.post('/api/customer-ai/review', json={'results': results,
        'selected_ids': identifiers or [item['customer_id'] for item in results['items']]})
    assert response.status_code == 200, response.text
    return response.json()


def apply_payload(results, **updates):
    return {'idempotency_key': str(uuid4()), 'batch_id': results['batch_id'], 'result': results['items'][0],
        'overwrite_description': False, 'overwrite_website': False, **updates}


def counts(database):
    with database.session() as session:
        return [session.scalar(select(func.count()).select_from(model)) for model in (DirectoryCommand, DirectoryChangeEvent)]


def exercise_customer_ai(database, client):
    """Shared SQLite/PostgreSQL persistence and preserving adoption acceptance."""
    customer = create_customer(client, name='Customer AI ' + uuid4().hex, brand_identifier='AI-' + uuid4().hex,
        description='Existing human profile.', website='https://existing.example/', notes='PRIVATE_NOTE', country='CZ',
        legal_name='PRIVATE_LEGAL_NAME', address='PRIVATE_ADDRESS', shipping_address='PRIVATE_SHIPPING', vat_id='PRIVATE_VAT', is_published=True)
    path = '/api/customers/' + customer['id']
    baseline = counts(database)
    exported = brief(client, [customer['id']]); results = results_for(exported)
    assert exported['schema_version'] == 'reawote-customer-ai-brief-v1'
    assert 'PRIVATE_' not in str(exported)
    row = review(client, results)['items'][0]
    assert row['status'] == 'OVERWRITE_REQUIRED' and row['requires_description_overwrite'] and row['requires_website_overwrite']
    assert counts(database) == baseline  # Export and review are read-only.
    payload = apply_payload(results)
    assert client.post(path + '/ai-brief-result', json=payload).json()['detail']['code'] == 'AI_DESCRIPTION_OVERWRITE_REQUIRED'
    payload['overwrite_description'] = True
    assert client.post(path + '/ai-brief-result', json=payload).json()['detail']['code'] == 'AI_WEBSITE_OVERWRITE_REQUIRED'
    assert counts(database) == baseline
    payload['overwrite_website'] = True
    response = client.post(path + '/ai-brief-result', json=payload)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt['description'] == results['items'][0]['description'] and receipt['website'] == results['items'][0]['website']
    current = client.get(path).json()
    for field in ('id', 'name', 'brand_identifier', 'folder_prefix', 'country', 'notes', 'legal_name', 'address', 'shipping_address', 'vat_id', 'is_active', 'is_published', 'notion_page_id'):
        assert current[field] == customer[field]
    assert counts(database) == [baseline[0] + 1, baseline[1] + 1]
    with database.session() as session:
        command = session.scalar(select(DirectoryCommand).where(DirectoryCommand.request_key == UUID(payload['idempotency_key'])))
        evidence = command.response_snapshot['_customer_ai_brief']['provenance']
        assert evidence['source_urls'] == results['items'][0]['source_urls'] and evidence['sources_verified'] is False
        sync = session.scalar(select(NotionSyncState).where(NotionSyncState.entity_id == UUID(customer['id'])))
        assert sync.payload['description'] == receipt['description'] and sync.payload['website'] == receipt['website']
        assert sync.status == 'PENDING' and sync.revision == 2
    history = client.get(path + '/history').json()['items']
    assert any(item['after']['description'] == receipt['description'] and item['before'].get('description') == customer['description'] for item in history)
    later = write(client, 'patch', path, {'expected_updated_at': current['updated_at'], 'description': 'Later manual edit.', 'website': 'https://later.example/'})
    assert later.status_code == 200, later.text
    now_counts = counts(database)
    assert client.post(path + '/ai-brief-result', json=payload).json() == receipt
    assert client.post(path + '/ai-brief-result', json={**payload, 'idempotency_key': str(uuid4())}).json() == receipt
    assert review(client, results)['items'][0]['status'] == 'ALREADY_APPLIED'
    assert counts(database) == now_counts
    assert client.get(path).json()['description'] == 'Later manual edit.'
    changed = deepcopy(payload); changed['result']['description'] = 'Changed proposal.'
    assert client.post(path + '/ai-brief-result', json=changed).status_code == 409
    current = client.get(path).json()
    inactive = write(client, 'patch', path, {'expected_updated_at': current['updated_at'], 'is_active': False})
    assert inactive.status_code == 200, inactive.text
    after_inactivation = counts(database)
    assert review(client, results)['items'][0]['status'] == 'ALREADY_APPLIED'
    assert client.post(path + '/ai-brief-result', json={**payload, 'idempotency_key': str(uuid4())}).json() == receipt
    assert counts(database) == after_inactivation
    assert client.get(path).json()['description'] == 'Later manual edit.'
    assert client.get(path).json()['is_active'] is False
    return customer['id'], results, receipt


def test_preserving_adoption_history_outbox_and_exact_restart_reimport(access_case):
    case = access_case
    with case.client('ADMIN') as client:
        identifier, results, receipt = exercise_customer_ai(case.database, client)
    baseline = counts(case.database)
    case.app = create_app(case.app.state.settings, case.database, case.worker)
    with case.client('PRODUCTION_LEAD') as client:
        assert review(client, results)['items'][0]['status'] == 'ALREADY_APPLIED'
        assert client.post('/api/customers/' + identifier + '/ai-brief-result', json=apply_payload(results)).json() == receipt
        assert client.get('/api/customers/' + identifier).json()['website'] == 'https://later.example/'
    assert counts(case.database) == baseline


@pytest.mark.parametrize('role,status', [(None, 401), ('PROCESSOR', 403), ('OTHER', 403), ('LEADERSHIP', 403), ('ADMIN', 200), ('PRODUCTION_LEAD', 200)])
def test_roles_and_csrf(access_case, role, status):
    identifier = str(access_case.materials[0].published_brand_id)
    with access_case.client('ADMIN') as client:
        exported = brief(client, [identifier]); results = results_for(exported)
        selection = [{'id': identifier, 'expected_updated_at': exported['items'][0]['expected_updated_at']}]
    with access_case.client(role) as client:
        assert client.post('/api/customer-ai/brief', json={'selection': selection}).status_code == status
        assert client.post('/api/customer-ai/review', json={'results': results, 'selected_ids': [identifier]}).status_code == status
        assert client.post('/api/customers/' + identifier + '/ai-brief-result', json=apply_payload(results)).status_code == status
        if role:
            client.headers.pop('X-CSRF-Token')
            for path in ('/api/customer-ai/brief', '/api/customer-ai/review', '/api/customers/' + identifier + '/ai-brief-result'):
                assert client.post(path, json={}).status_code == 403


def test_null_fields_keep_current_values_website_only_updates_need_separate_consent(access_case):
    with access_case.client('ADMIN') as client:
        customer = create_customer(client, description='Keep profile', website='https://old.example/')
        results = results_for(brief(client, [customer['id']]), description=None)
        row = review(client, results)['items'][0]
        assert row['requires_website_overwrite'] and not row['requires_description_overwrite']
        response = client.post('/api/customers/' + customer['id'] + '/ai-brief-result', json=apply_payload(results, overwrite_website=True))
        assert response.status_code == 200 and response.json()['description'] == 'Keep profile'
        next_results = results_for(brief(client, [customer['id']]), website=' ', description='New description')
        reviewed = review(client, next_results)['items'][0]
        assert reviewed['result']['website'] is None and not reviewed['requires_website_overwrite']
        payload = apply_payload(next_results, overwrite_description=True); payload['result'] = reviewed['result']
        response = client.post('/api/customers/' + customer['id'] + '/ai-brief-result', json=payload)
        assert response.status_code == 200 and response.json()['website'] == 'https://official.example/'


def test_stale_uncertain_missing_inactive_empty_and_nochange_results_are_blocked(access_case):
    case = access_case; identifier = str(case.materials[0].published_brand_id)
    with case.client('ADMIN') as client:
        exported = brief(client, [identifier]); results = results_for(exported)
        for patch, status in [({'needs_review': True}, 'NEEDS_REVIEW'), ({'description': None, 'website': None}, 'EMPTY_PROPOSAL')]:
            bad = deepcopy(results); bad['items'][0].update(patch)
            assert review(client, bad)['items'][0]['status'] == status
            assert client.post('/api/customers/' + identifier + '/ai-brief-result', json=apply_payload(bad)).status_code in {409, 422}
        missing = str(uuid4())
        assert review(client, results, [identifier, missing])['items'][1]['status'] == 'CUSTOMER_UNAVAILABLE'
        current = client.get('/api/customers/' + identifier).json()
        response = write(client, 'patch', '/api/customers/' + identifier, {'expected_updated_at': current['updated_at'], 'notes': 'Changed context'})
        assert response.status_code == 200
        assert review(client, results)['items'][0]['status'] == 'STALE_CONTEXT'
        assert client.post('/api/customers/' + identifier + '/ai-brief-result', json=apply_payload(results)).status_code == 409
        now = client.get('/api/customers/' + identifier).json()
        write(client, 'patch', '/api/customers/' + identifier, {'expected_updated_at': now['updated_at'], 'description': 'Existing'})
        nochange = results_for(brief(client, [identifier]), description='Existing', website=None)
        assert review(client, nochange)['items'][0]['status'] == 'NO_CHANGES'
        with case.database.session() as session:
            session.get(PublishedBrand, UUID(identifier)).is_active = False; session.commit()
        assert review(client, nochange)['items'][0]['status'] == 'CUSTOMER_INACTIVE'
        assert brief(client, [identifier])['skipped'][0]['code'] == 'CUSTOMER_INACTIVE'


def test_bounded_strict_json_safe_urls_selection_and_no_reflected_private_values(access_case):
    identifier = str(access_case.materials[0].published_brand_id)
    with access_case.client('ADMIN') as client:
        exported = brief(client, [identifier]); results = results_for(exported)
        selected = {'id': identifier, 'expected_updated_at': exported['items'][0]['expected_updated_at']}
        for selection in ([], [selected, selected], [{**selected, 'id': str(uuid4())} for _ in range(101)]):
            assert client.post('/api/customer-ai/brief', json={'selection': selection}).status_code == 422
        assert client.post('/api/customer-ai/brief', json={'selection': [{**selected, 'expected_updated_at': (datetime.now(UTC) - timedelta(days=1)).isoformat()}]}).status_code == 409
        for changes in ({'website': 'http://PRIVATE.example/'}, {'website': 'https://127.0.0.1/PRIVATE'},
            {'website': 'https://user:PRIVATE@site.example/'}, {'source_urls': ['https://host.local/PRIVATE']},
            {'description': 'PRIVATE\u200btext'}, {'note': 'PRIVATE' * 400}, {'description': 'PRIVATE' * 4000},
            {'expected_updated_at': '2026-01-01T00:00:00'}, {'PRIVATE': 'unexpected'}, {'needs_review': 'false'}):
            invalid = deepcopy(results); invalid['items'][0].update(changes)
            response = client.post('/api/customer-ai/review', json={'results': invalid, 'selected_ids': [identifier]})
            assert response.status_code == 422 and 'PRIVATE' not in response.text
        duplicated = deepcopy(results); duplicated['items'] *= 2
        assert client.post('/api/customer-ai/review', json={'results': duplicated, 'selected_ids': [identifier]}).status_code == 422
        assert client.post('/api/customer-ai/review', json={'results': results, 'selected_ids': [str(uuid4())]}).status_code == 422
        assert client.post('/api/customer-ai/review', content=b' ' * (5 * 1024**2 + 1), headers={'Content-Type': 'application/json'}).status_code == 413
        assert client.post('/api/customer-ai/brief', content='{"PRIVATE":1,"PRIVATE":2}', headers={'Content-Type': 'application/json'}).status_code == 422


def test_microseconds_are_preserved_and_missing_busy_customers_are_not_applied(access_case, monkeypatch):
    from fastapi import HTTPException
    identifier = str(access_case.materials[0].published_brand_id)
    with access_case.database.session() as session:
        session.get(PublishedBrand, UUID(identifier)).updated_at = datetime(2026, 10, 6, 12, 13, 14, 123456, tzinfo=UTC)
        session.commit()
    with access_case.client('ADMIN') as client:
        exported = brief(client, [identifier]); results = results_for(exported)
        assert results['items'][0]['expected_updated_at'].endswith('.123456+00:00')
        assert review(client, results)['items'][0]['result']['expected_updated_at'] == results['items'][0]['expected_updated_at']
        def busy(*args): raise HTTPException(409, {'code': 'CUSTOMER_RENAME_ACTIVE'})
        monkeypatch.setattr('app.customer_ai_brief.require_brand_idle', busy)
        assert brief(client, [identifier])['skipped'][0]['code'] == 'CUSTOMER_BUSY'
        assert review(client, results)['items'][0]['status'] == 'CUSTOMER_BUSY'
        assert client.post('/api/customers/' + identifier + '/ai-brief-result', json=apply_payload(results)).json()['detail']['code'] == 'CUSTOMER_BUSY'
        missing = deepcopy(results); missing['items'][0]['customer_id'] = str(uuid4())
        assert review(client, missing)['items'][0]['status'] == 'CUSTOMER_UNAVAILABLE'
        assert client.post('/api/customers/' + missing['items'][0]['customer_id'] + '/ai-brief-result', json=apply_payload(missing)).status_code == 404
        assert client.post('/api/customers/' + str(uuid4()) + '/ai-brief-result', json=apply_payload(results)).status_code == 422
