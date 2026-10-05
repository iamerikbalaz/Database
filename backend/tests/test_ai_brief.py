"""AI JSON handoff uses actual auth, bounded input and atomic content receipts."""
import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from starlette.requests import Request

from app.api.ai_brief import BriefRequestReader, MAX_REQUEST_BYTES
from app.db.models import (MaterialAiDraft, MaterialAuditEvent, MaterialContent, MaterialContentRevision,
    MaterialSourceLink, PBRMaterial, PublishedBrand)
from test_application_access import access_case
from test_catalog_content import content_payload, create_vocabulary


def export(client, materials):
    selection = []
    for material in materials:
        current = client.get(f'/api/materials/{material.id}')
        assert current.status_code == 200, current.text
        selection.append({'id': str(material.id), 'expected_updated_at': current.json()['updated_at']})
    response = client.post('/api/material-ai/brief', json={'selection': selection})
    assert response.status_code == 200, response.text
    return response.json()


def results_for(brief, **updates):
    result = deepcopy(brief['result_template'])
    for item in result['items']:
        item.update(description='A sourced synthetic surface description.', source_urls=['https://catalog.example/material/synthetic'],
            needs_review=False, note='Material identified in the cited public product page.', **updates)
    return result


def review(client, brief, results):
    response = client.post('/api/material-ai/review', json={'results': results,
        'selected_ids': [item['material_id'] for item in brief['items']]})
    assert response.status_code == 200, response.text
    return response.json()


def application_payload(results, *, overwrite=False):
    return {'idempotency_key': str(uuid4()), 'batch_id': results['batch_id'], 'result': results['items'][0], 'overwrite': overwrite}


def counts(database):
    with database.session() as session:
        return [session.scalar(select(func.count()).select_from(model))
            for model in (MaterialAiDraft, MaterialContentRevision, MaterialAuditEvent, MaterialSourceLink)]


def exercise_content_round_trip(case, client):
    """Shared SQLite and real migrated PostgreSQL acceptance."""
    material = case.materials[0]; path = f'/api/materials/{material.id}'
    category, collection = create_vocabulary(client, material.published_brand_id)
    initial = client.post(path + '/content', json=content_payload(description='Existing human description.', credits=7,
        tags=['retained tag'], category_ids=[category['id']], collection_ids=[collection['id']]))
    assert initial.status_code == 200, initial.text
    before = initial.json(); baseline = counts(case.database)
    brief = export(client, [material]); results = results_for(brief)
    row = review(client, brief, results)['items'][0]
    assert row['status'] == 'OVERWRITE_REQUIRED' and row['applicable'] and row['requires_overwrite']
    assert row['current_description'] == before['description'] and row['result'] == results['items'][0]
    assert counts(case.database) == baseline
    payload = application_payload(results)
    assert client.post(path + '/ai-brief-result', json=payload).json()['detail']['code'] == 'AI_OVERWRITE_REQUIRED'
    assert counts(case.database) == baseline
    payload['overwrite'] = True
    applied = client.post(path + '/ai-brief-result', json=payload)
    assert applied.status_code == 200, applied.text
    receipt = applied.json()
    assert receipt['status'] == 'APPLIED' and receipt['content_revision'] == before['revision'] + 1
    saved = client.get(path + '/content').json()
    for key in ('tags', 'credits', 'categories', 'collections'):
        assert saved[key] == before[key]
    assert saved['description'] == results['items'][0]['description']
    assert saved['content_status'] == 'AI_DRAFT'
    assert saved['ai_provenance']['sources_verified'] is False
    assert saved['ai_provenance']['source_urls'] == results['items'][0]['source_urls']
    assert client.get(path + '/content-approvals').json() == []
    assert counts(case.database) == [baseline[0] + 1, baseline[1] + 1, baseline[2] + 1, baseline[3]]
    with case.database.session() as session:
        draft = session.get(MaterialAiDraft, UUID(receipt['draft_id']))
        assert draft.source_link_ids == []
        assert draft.context['json_result'] == {'batch_id': results['batch_id'], 'source_urls': results['items'][0]['source_urls'],
            'needs_review': False, 'note': results['items'][0]['note'], 'sources_verified': False}
        event = session.scalar(select(MaterialAuditEvent).where(MaterialAuditEvent.request_key == UUID(payload['idempotency_key'])))
        assert event.result['audit']['source_urls'] == results['items'][0]['source_urls']
        revision = session.scalar(select(MaterialContentRevision).where(MaterialContentRevision.material_id == material.id,
            MaterialContentRevision.revision == receipt['content_revision']))
        assert revision.snapshot['ai_provenance']['draft_id'] == str(draft.id)
    activity = client.get(path + '/activity').json()['items']
    assert sum(item['source'] == 'content' for item in activity) == 2
    assert not any(item['action'] == 'AI_BRIEF_RESULT_APPLIED' for item in activity)
    later = client.post(path + '/content', json=content_payload(expected_revision=receipt['content_revision'],
        description='A later human edit.', credits=7, tags=['retained tag'], category_ids=[category['id']], collection_ids=[collection['id']]))
    assert later.status_code == 200, later.text
    current_counts = counts(case.database)
    assert client.post(path + '/ai-brief-result', json=payload).json() == receipt
    assert counts(case.database) == current_counts
    assert client.get(path + '/content').json()['description'] == 'A later human edit.'
    changed = deepcopy(payload); changed['result']['description'] = 'Changed proposal with reused key.'
    assert client.post(path + '/ai-brief-result', json=changed).status_code == 409
    assert counts(case.database) == current_counts
    assert review(client, brief, results)['items'][0]['status'] == 'STALE_CONTEXT'
    return receipt


def test_apply_preserves_content_provenance_and_exact_retry_after_later_edit(access_case):
    with access_case.client('ADMIN') as client:
        receipt = exercise_content_round_trip(access_case, client)
    with access_case.database.session() as session:
        draft = session.get(MaterialAiDraft, UUID(receipt['draft_id'])); draft.description = 'Forbidden mutation.'
        with pytest.raises(Exception, match='append-only'): session.commit()


@pytest.mark.parametrize('change', ['archived', 'deleted'])
def test_exact_success_receipt_survives_archive_or_deletion_but_new_write_is_denied(access_case, change):
    case = access_case; material = case.materials[0]; path = f'/api/materials/{material.id}/ai-brief-result'
    with case.client('ADMIN') as client:
        payload = application_payload(results_for(export(client, [material])))
        first = client.post(path, json=payload)
        assert first.status_code == 200, first.text
        if change == 'archived':
            from test_material_archives import apply as archive, prepare as prepare_archive
            response = archive(client, material.id, prepare_archive(client, material.id))
            assert response.status_code == 200, response.text
        else:
            with case.database.session() as session:
                session.get(PBRMaterial, material.id).deleted_at = datetime.now(UTC)
                session.commit()
        baseline = counts(case.database)
        replay = client.post(path, json=payload)
        assert replay.status_code == 200 and replay.json() == first.json()
        assert client.post(path, json={**payload, 'idempotency_key': str(uuid4())}).status_code == 404
        assert counts(case.database) == baseline


def test_export_is_minimal_public_context_with_explicit_draft_skips_and_no_writes(access_case):
    case = access_case; first, second = case.materials
    with case.database.session() as session:
        material = session.get(PBRMaterial, first.id); material.note = 'PRIVATE_NOTE'; material.folder_path = 'PRIVATE_PATH/material'
        brand = session.get(PublishedBrand, first.published_brand_id)
        brand.website = 'https://CATALOG.example:443/materials'; brand.address = 'PRIVATE_ADDRESS'; brand.notes = 'PRIVATE_CUSTOMER_NOTES'
        draft = session.get(PBRMaterial, second.id)
        draft.is_draft = True; draft.technical_identity = None; draft.sequence_number = None; draft.main_category_code = None; draft.published_brand_id = None
        session.commit()
    baseline = counts(case.database)
    with case.client('PRODUCTION_LEAD') as client:
        brief = export(client, [first, second])
        assert brief['schema_version'] == 'reawote-ai-brief-v1'
        assert len(brief['items']) == 1 and brief['skipped'][0]['material_id'] == str(second.id)
        assert brief['skipped'][0]['code'] == 'MATERIAL_IDENTITY_INCOMPLETE'
        item = brief['items'][0]
        assert item['context']['customer']['website'] == 'https://catalog.example/materials'
        assert item['context_hash'] == client.get(f'/api/materials/{first.id}/publishing-context').json()['context_hash']
        serialized = json.dumps(brief)
        for value in ('PRIVATE_', 'folder_path', 'assigned_processor_id', 'technical_identity', 'address', 'email'):
            assert value not in serialized
        assert brief['result_json_schema']['additionalProperties'] is False
        assert brief['result_template']['items'][0]['needs_review'] is True
    assert counts(case.database) == baseline


@pytest.mark.parametrize('role,expected', [(None, 401), ('PROCESSOR', 403), ('OTHER', 403), ('LEADERSHIP', 403), ('ADMIN', 200), ('PRODUCTION_LEAD', 200)])
def test_brief_workflow_requires_manager_authentication(access_case, role, expected):
    case = access_case; material = case.materials[0]
    with case.client('ADMIN') as client:
        brief = export(client, [material]); results = results_for(brief)
        selection = {'selection': [{'id': str(material.id), 'expected_updated_at': client.get(f'/api/materials/{material.id}').json()['updated_at']}]}
    with case.client(role) as client:
        assert client.post('/api/material-ai/brief', json=selection).status_code == expected
        assert client.post('/api/material-ai/review', json={'results': results, 'selected_ids': [str(material.id)]}).status_code == expected
        assert client.post(f'/api/materials/{material.id}/ai-brief-result', json=application_payload(results)).status_code == expected


def test_csrf_and_selection_bounds_duplicate_ids_stale_and_unknown_are_explicit(access_case):
    case = access_case; material = case.materials[0]
    with case.client('ADMIN') as client:
        timestamp = client.get(f'/api/materials/{material.id}').json()['updated_at']
        selected = {'id': str(material.id), 'expected_updated_at': timestamp}
        for selection in ([], [selected, selected], [{'id': str(uuid4()), 'expected_updated_at': timestamp} for _ in range(101)]):
            assert client.post('/api/material-ai/brief', json={'selection': selection}).status_code == 422
        stale = {**selected, 'expected_updated_at': (datetime.now(UTC) - timedelta(days=100)).isoformat()}
        assert client.post('/api/material-ai/brief', json={'selection': [stale]}).json()['detail']['code'] == 'AI_SELECTION_CHANGED'
        assert client.post('/api/material-ai/brief', json={'selection': [{**selected, 'id': str(uuid4())}]}).status_code == 404
        client.headers.pop('X-CSRF-Token')
        assert client.post('/api/material-ai/brief', json={'selection': [selected]}).status_code == 403
        assert client.post('/api/material-ai/review', json={}).status_code == 403
        assert client.post(f'/api/materials/{material.id}/ai-brief-result', json={}).status_code == 403


def test_review_rejects_foreign_results_and_invalid_private_payloads_without_echo(access_case):
    case = access_case; material = case.materials[0]
    with case.client('ADMIN') as client:
        brief = export(client, [material]); results = results_for(brief)
        foreign = deepcopy(results); foreign['items'][0]['material_id'] = str(case.materials[1].id)
        response = client.post('/api/material-ai/review', json={'results': foreign, 'selected_ids': [str(material.id)]})
        assert response.status_code == 422 and response.json()['detail']['code'] == 'AI_RESULT_OUTSIDE_SELECTION'
        invalid_updates = [{'PRIVATE_KEY': 'PRIVATE_VALUE'}, {'needs_review': 'false'}, {'content_revision': True},
            {'source_urls': ['http://catalog.example/PRIVATE']}, {'source_urls': ['https://user:PRIVATE@catalog.example/']},
            {'source_urls': ['https://catalog.example/?token=PRIVATE']}, {'source_urls': ['https://127.0.0.1/PRIVATE']},
            {'description': 'PRIVATE\u200bVALUE'}, {'note': 'PRIVATE' * 400}, {'description': 'PRIVATE' * 2000}]
        for update in invalid_updates:
            invalid = deepcopy(results); invalid['items'][0].update(update)
            response = client.post('/api/material-ai/review', json={'results': invalid, 'selected_ids': [str(material.id)]})
            assert response.status_code == 422 and 'PRIVATE' not in response.text
        duplicate = deepcopy(results); duplicate['items'] *= 2
        assert client.post('/api/material-ai/review', json={'results': duplicate, 'selected_ids': [str(material.id)]}).status_code == 422
        assert client.post('/api/material-ai/review', json={'results': results, 'selected_ids': [str(material.id)] * 2}).status_code == 422
        assert client.post('/api/material-ai/review', content='{"PRIVATE":1,"PRIVATE":2}', headers={'Content-Type': 'application/json'}).status_code == 422


def test_uncertain_empty_missing_and_changed_context_cannot_apply(access_case):
    case = access_case; material = case.materials[0]; path = f'/api/materials/{material.id}/ai-brief-result'
    baseline = counts(case.database)
    with case.client('ADMIN') as client:
        brief = export(client, case.materials); results = results_for(brief)
        results['items'] = results['items'][:1]
        rows = review(client, brief, results)['items']
        assert [item['status'] for item in rows] == ['READY', 'MISSING_RESULT']
        for updates, status, code in [({'needs_review': True}, 'NEEDS_REVIEW', 'AI_RESULT_NEEDS_REVIEW'),
                                     ({'description': None}, 'EMPTY_DESCRIPTION', 'AI_DESCRIPTION_REQUIRED')]:
            bad = deepcopy(results); bad['items'][0].update(updates)
            assert review(client, brief, bad)['items'][0]['status'] == status
            assert client.post(path, json=application_payload(bad)).json()['detail']['code'] == code
        with case.database.session() as session:
            brand = session.get(PublishedBrand, material.published_brand_id)
            brand.website = 'https://changed.example/materials'; brand.updated_at = datetime.now(UTC) + timedelta(seconds=1)
            session.commit()
        assert review(client, brief, results)['items'][0]['status'] == 'STALE_CONTEXT'
        assert client.post(path, json=application_payload(results)).json()['detail']['code'] == 'AI_CONTEXT_CHANGED'
        mismatch = application_payload(results)
        assert client.post(f'/api/materials/{case.materials[1].id}/ai-brief-result', json=mismatch).status_code == 422
    assert counts(case.database) == baseline


def test_inactive_customer_and_busy_material_are_skipped_or_blocked(access_case, monkeypatch):
    case = access_case; material = case.materials[0]
    with case.client('ADMIN') as client:
        brief = export(client, [material]); results = results_for(brief)
        with case.database.session() as session:
            session.get(PublishedBrand, material.published_brand_id).is_active = False; session.commit()
        assert export(client, [material])['skipped'][0]['code'] == 'CUSTOMER_INACTIVE'
        assert review(client, brief, results)['items'][0]['status'] == 'CUSTOMER_INACTIVE'
        assert client.post(f'/api/materials/{material.id}/ai-brief-result', json=application_payload(results)).json()['detail']['code'] == 'CUSTOMER_INACTIVE'
        with case.database.session() as session:
            session.get(PublishedBrand, material.published_brand_id).is_active = True; session.commit()
        from fastapi import HTTPException
        def busy(*args): raise HTTPException(409, {'code': 'MATERIAL_OPERATION_ACTIVE'})
        monkeypatch.setattr('app.ai_brief.require_material_idle', busy)
        assert export(client, [material])['skipped'][0]['code'] == 'MATERIAL_BUSY'
        assert review(client, brief, results)['items'][0]['status'] == 'MATERIAL_BUSY'
        assert client.post(f'/api/materials/{material.id}/ai-brief-result', json=application_payload(results)).json()['detail']['code'] == 'MATERIAL_BUSY'


def test_server_rejects_oversized_body_and_unsupported_encoding_before_parse(access_case):
    with access_case.client('ADMIN') as client:
        response = client.post('/api/material-ai/review', content=b' ' * (MAX_REQUEST_BYTES + 1), headers={'Content-Type': 'application/json'})
        assert response.status_code == 413
        assert client.post('/api/material-ai/brief', content=b'{}', headers={'Content-Type': 'application/json', 'Content-Encoding': 'gzip'}).status_code == 415
        assert client.post('/api/material-ai/brief', content=b'{}', headers={'Content-Type': 'text/plain'}).status_code == 415


def test_chunked_reader_caps_bytes_and_releases_capacity_without_content_length():
    from fastapi import HTTPException
    reader = BriefRequestReader()
    async def run():
        chunks = iter([b'x' * (MAX_REQUEST_BYTES - 1), b'xx'])
        async def receive(): return {'type': 'http.request', 'body': next(chunks), 'more_body': True}
        request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [(b'content-type', b'application/json')]}, receive)
        with pytest.raises(HTTPException) as error:
            async with reader.body(request): pytest.fail('Oversized body was accepted')
        assert error.value.status_code == 413
    asyncio.run(run())
    assert reader.slots.acquire(False) and reader.slots.acquire(False)
    reader.slots.release(); reader.slots.release()
