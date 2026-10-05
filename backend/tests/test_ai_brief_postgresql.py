"""Real migrated PostgreSQL evidence for immutable JSON adoption and receipts."""
from uuid import uuid4

import pytest

from app.auth.security import PasswordService
from app.core.config import Settings
from app.db.models import Company, InternalUser, PBRMaterial, PBRMaterialMetadata, Project, PublishedBrand, UserCredential
from app.db.session import Database
from app.main import create_app
from test_ai_brief import exercise_content_round_trip
from test_application_access import AccessCase, ORIGIN, PASSWORD
from test_auth_postgresql import POSTGRES_TEST_ADMIN_URL, auth_postgresql_url

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None,
    reason='POSTGRES_TEST_ADMIN_URL is required for the real PostgreSQL AI brief test')]


def test_postgresql_ai_brief_readonly_review_and_atomic_preserving_adoption_replay(auth_postgresql_url):
    database = Database(auth_postgresql_url)
    settings = Settings(_env_file=None, app_env='test', database_url=auth_postgresql_url, cors_origins=ORIGIN,
        auth_rate_limit_attempts=100)
    suffix = uuid4().hex
    with database.session() as session:
        actor = InternalUser(display_name='AI brief test administrator', email=f'ai-brief-{suffix}@example.invalid', role='ADMIN')
        actor.credential = UserCredential(password_hash=PasswordService(settings).hash_password(PASSWORD), must_change_password=False)
        company = Company(name='Synthetic AI brief company ' + suffix)
        session.add_all([actor, company]); session.flush()
        project = Project(company_id=company.id, project_number='AI-BRIEF-' + suffix, name='Synthetic AI brief project')
        brand = PublishedBrand(company_id=company.id, name='Synthetic AI brief customer', folder_prefix='BRIEF' + suffix,
            brand_identifier='ai-brief-' + suffix, next_sequence_number=2, website='https://catalog.example/synthetic')
        session.add_all([project, brand]); session.flush()
        material = PBRMaterial(project_id=project.id, published_brand_id=brand.id, assigned_processor_id=actor.id,
            sequence_number=1, main_category_code='G03', material_name='Synthetic AI brief material', technical_identity='BRIEF_' + suffix + '_0001_G03')
        material.metadata_state = PBRMaterialMetadata()
        session.add(material); session.commit()
    case = AccessCase(database, create_app(settings, database), None, {'ADMIN': actor}, [material])
    try:
        with case.client('ADMIN') as client:
            exercise_content_round_trip(case, client)
    finally:
        database.dispose()
