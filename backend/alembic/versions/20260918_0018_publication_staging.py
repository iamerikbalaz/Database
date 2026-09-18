"""Durable internal staging reservations and exclusive material ownership."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260918_0018"
down_revision = "20260918_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('publication_staging_jobs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('batch_id', sa.Uuid(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=False),
    sa.Column('issuer_session_id', sa.Uuid(), nullable=False),
    sa.Column('request_key', sa.Uuid(), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('material_count', sa.Integer(), nullable=False),
    sa.Column('bucket_name', sa.String(length=63), nullable=False),
    sa.Column('staging_prefix', sa.String(length=128), nullable=False),
    sa.Column('plan_sha256', sa.String(length=64), nullable=False),
    sa.Column('plan', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("plan_sha256 IS NULL OR (length(plan_sha256) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(plan_sha256, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_staging_jobs_plan_sha256'),
    sa.CheckConstraint("request_hash IS NULL OR (length(request_hash) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(request_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_staging_jobs_request_hash'),
    sa.CheckConstraint('length(bucket_name) BETWEEN 3 AND 63', name='ck_staging_jobs_bucket'),
    sa.CheckConstraint('length(reason) BETWEEN 1 AND 2000', name='ck_staging_jobs_reason'),
    sa.CheckConstraint('length(staging_prefix) BETWEEN 1 AND 128', name='ck_staging_jobs_prefix'),
    sa.CheckConstraint('material_count BETWEEN 1 AND 100', name='ck_staging_jobs_count'),
    sa.ForeignKeyConstraint(['actor_id'], ['internal_users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['batch_id'], ['publication_batches.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('actor_id', 'request_key', name='uq_staging_jobs_request'),
    sa.UniqueConstraint('id', 'batch_id', name='uq_staging_jobs_batch')
    )
    op.create_index(op.f('ix_publication_staging_jobs_batch_id'), 'publication_staging_jobs', ['batch_id'], unique=False)
    op.create_index(op.f('ix_publication_staging_jobs_created_at'), 'publication_staging_jobs', ['created_at'], unique=False)
    op.create_table('publication_staging_closes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=False),
    sa.Column('issuer_session_id', sa.Uuid(), nullable=False),
    sa.Column('request_key', sa.Uuid(), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("request_hash IS NULL OR (length(request_hash) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(request_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_staging_closes_request_hash'),
    sa.CheckConstraint('length(reason) BETWEEN 1 AND 2000', name='ck_staging_closes_reason'),
    sa.ForeignKeyConstraint(['actor_id'], ['internal_users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id'], ['publication_staging_jobs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('actor_id', 'request_key', name='uq_staging_closes_request'),
    sa.UniqueConstraint('job_id', 'id', name='uq_staging_closes_binding'),
    sa.UniqueConstraint('job_id', name='uq_staging_closes_job')
    )
    op.create_table('publication_staging_items',
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('material_id', sa.Uuid(), nullable=False),
    sa.Column('batch_id', sa.Uuid(), nullable=False),
    sa.Column('execution_id', sa.Uuid(), nullable=False),
    sa.Column('observation_id', sa.Uuid(), nullable=False),
    sa.Column('brand_id', sa.Uuid(), nullable=False),
    sa.Column('folder_path', sa.String(length=2048), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('worker_request_hash', sa.String(length=64), nullable=False),
    sa.Column('packaging_proof_sha256', sa.String(length=64), nullable=False),
    sa.CheckConstraint("input_hash IS NULL OR (length(input_hash) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(input_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_staging_items_input_hash'),
    sa.CheckConstraint("packaging_proof_sha256 IS NULL OR (length(packaging_proof_sha256) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(packaging_proof_sha256, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_staging_items_packaging_proof_sha256'),
    sa.CheckConstraint("worker_request_hash IS NULL OR (length(worker_request_hash) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(worker_request_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_staging_items_worker_request_hash'),
    sa.CheckConstraint('length(folder_path) BETWEEN 1 AND 2048', name='ck_staging_items_folder'),
    sa.ForeignKeyConstraint(['batch_id', 'material_id'], ['publication_batch_items.batch_id', 'publication_batch_items.material_id'], name='fk_staging_items_batch', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['brand_id'], ['published_brands.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['execution_id', 'observation_id'], ['material_packaging_observations.execution_id', 'material_packaging_observations.id'], name='fk_staging_items_observation', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id', 'batch_id'], ['publication_staging_jobs.id', 'publication_staging_jobs.batch_id'], name='fk_staging_items_job', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['material_id', 'execution_id'], ['material_packaging_executions.material_id', 'material_packaging_executions.id'], name='fk_staging_items_execution', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('job_id', 'material_id')
    )
    op.create_index(op.f('ix_publication_staging_items_brand_id'), 'publication_staging_items', ['brand_id'], unique=False)
    op.create_table('publication_staging_owners',
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('material_id', sa.Uuid(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('close_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(active AND close_id IS NULL) OR (NOT active AND close_id IS NOT NULL)', name='ck_staging_owners_state'),
    sa.ForeignKeyConstraint(['job_id', 'close_id'], ['publication_staging_closes.job_id', 'publication_staging_closes.id'], name='fk_staging_owners_close', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id', 'material_id'], ['publication_staging_items.job_id', 'publication_staging_items.material_id'], name='fk_staging_owners_item', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('job_id', 'material_id')
    )
    op.create_index('uq_staging_owners_active', 'publication_staging_owners', ['material_id'], unique=True, postgresql_where=sa.text('active'), sqlite_where=sa.text('active'))
    # Immutable reservation facts survive closure; no cloud dispatch exists yet.
    for table in ("publication_staging_jobs", "publication_staging_items", "publication_staging_closes"):
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    op.execute("CREATE TRIGGER publication_staging_owners_preserve BEFORE DELETE OR TRUNCATE ON publication_staging_owners "
        "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    op.execute("""CREATE FUNCTION staging_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE batch publication_batches%ROWTYPE; body jsonb; csv jsonb; distinct_materials integer;
    BEGIN
        SELECT * INTO batch FROM publication_batches WHERE id=NEW.batch_id;
        body := NEW.plan->'body';
        IF batch.id IS NULL OR jsonb_typeof(NEW.plan) IS DISTINCT FROM 'object'
            OR length(NEW.plan::text)>33554432 OR NEW.plan->>'sha256' IS DISTINCT FROM NEW.plan_sha256
            OR jsonb_typeof(body) IS DISTINCT FROM 'object' OR body->'version' IS DISTINCT FROM '1'::jsonb
            OR body->>'layout' IS DISTINCT FROM 'INTERNAL_STAGING_V1'
            OR body->>'job_id' IS DISTINCT FROM NEW.id::text OR body->>'batch_id' IS DISTINCT FROM NEW.batch_id::text
            OR body->>'bucket_name' IS DISTINCT FROM NEW.bucket_name OR body->>'staging_prefix' IS DISTINCT FROM NEW.staging_prefix
            OR body->>'batch_snapshot_sha256' IS DISTINCT FROM batch.snapshot_hash
            OR NEW.material_count IS DISTINCT FROM batch.row_count
            OR jsonb_typeof(body->'materials') IS DISTINCT FROM 'array'
            OR jsonb_typeof(body->'objects') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'Staging job must bind the immutable batch and internal destination';
        END IF;
        SELECT count(DISTINCT value->>'material_id') INTO distinct_materials FROM jsonb_array_elements(body->'materials');
        IF jsonb_array_length(body->'materials')<>NEW.material_count OR distinct_materials<>NEW.material_count
            OR jsonb_array_length(body->'objects') NOT BETWEEN 3 AND 20001 THEN
            RAISE EXCEPTION 'Invalid staging plan cardinality';
        END IF;
        SELECT value INTO csv FROM jsonb_array_elements(body->'objects') WHERE value->>'relative_path'='publication.csv';
        IF csv IS NULL OR csv->>'sha256' IS DISTINCT FROM batch.csv_sha256
            OR csv->'material_id' IS DISTINCT FROM 'null'::jsonb OR csv->'source_path' IS DISTINCT FROM 'null'::jsonb
            OR csv->>'size' IS DISTINCT FROM octet_length(batch.csv_bytes)::text THEN
            RAISE EXCEPTION 'Staging plan must include the exact frozen CSV';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER staging_job_guard BEFORE INSERT ON publication_staging_jobs "
        "FOR EACH ROW EXECUTE FUNCTION staging_job_guard()")
    op.execute("""CREATE FUNCTION staging_item_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE job publication_staging_jobs%ROWTYPE; material pbr_materials%ROWTYPE;
        execution material_packaging_executions%ROWTYPE; state material_packaging_states%ROWTYPE;
        observed material_packaging_observations%ROWTYPE; binding jsonb;
    BEGIN
        SELECT * INTO material FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
        SELECT * INTO job FROM publication_staging_jobs WHERE id=NEW.job_id;
        SELECT * INTO execution FROM material_packaging_executions WHERE id=NEW.execution_id;
        SELECT * INTO state FROM material_packaging_states WHERE execution_id=NEW.execution_id;
        SELECT * INTO observed FROM material_packaging_observations WHERE id=NEW.observation_id;
        SELECT value INTO binding FROM jsonb_array_elements(job.plan->'body'->'materials')
            WHERE value->>'material_id'=NEW.material_id::text;
        IF material.id IS NULL OR job.id IS NULL OR execution.id IS NULL OR state.execution_id IS NULL
            OR observed.id IS NULL OR binding IS NULL OR job.batch_id IS DISTINCT FROM NEW.batch_id
            OR execution.material_id IS DISTINCT FROM NEW.material_id OR execution.batch_id IS DISTINCT FROM NEW.batch_id
            OR material.published_brand_id IS DISTINCT FROM NEW.brand_id OR material.folder_path IS DISTINCT FROM NEW.folder_path
            OR execution.folder_path IS DISTINCT FROM NEW.folder_path OR execution.input_hash IS DISTINCT FROM NEW.input_hash
            OR execution.worker_request_hash IS DISTINCT FROM NEW.worker_request_hash
            OR state.status IS DISTINCT FROM 'PACKAGED' OR state.last_observation_id IS DISTINCT FROM observed.id
            OR state.last_dispatch_id IS DISTINCT FROM observed.dispatch_id OR observed.execution_id IS DISTINCT FROM execution.id
            OR observed.outcome IS DISTINCT FROM 'READY' OR NOT observed.inputs_current OR NOT observed.actor_current
            OR observed.proof_sha256 IS DISTINCT FROM NEW.packaging_proof_sha256
            OR binding->>'execution_id' IS DISTINCT FROM execution.id::text
            OR binding->>'batch_item_sha256' IS DISTINCT FROM NEW.input_hash
            OR binding->>'worker_request_sha256' IS DISTINCT FROM NEW.worker_request_hash
            OR binding->>'packaging_proof_sha256' IS DISTINCT FROM NEW.packaging_proof_sha256
            OR EXISTS (SELECT 1 FROM publication_staging_closes WHERE job_id=NEW.job_id) THEN
            RAISE EXCEPTION 'Staging item must bind an accepted package and its planned material';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER staging_item_guard BEFORE INSERT ON publication_staging_items "
        "FOR EACH ROW EXECUTE FUNCTION staging_item_guard()")
    op.execute("""CREATE FUNCTION staging_owner_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM 1 FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
        IF TG_OP='UPDATE' AND (NEW.job_id IS DISTINCT FROM OLD.job_id OR NEW.material_id IS DISTINCT FROM OLD.material_id
            OR NEW.created_at IS DISTINCT FROM OLD.created_at OR (NOT OLD.active AND NEW IS DISTINCT FROM OLD)) THEN
            RAISE EXCEPTION 'Staging ownership identity and released state are immutable';
        END IF;
        IF TG_OP='INSERT' AND NOT NEW.active THEN
            RAISE EXCEPTION 'New staging ownership must be active';
        END IF;
        IF NEW.active AND (
            EXISTS (SELECT 1 FROM material_file_operations WHERE material_id=NEW.material_id AND status IN ('RUNNING','RECOVERY_REQUIRED'))
            OR EXISTS (SELECT 1 FROM material_packaging_states WHERE material_id=NEW.material_id
                AND status IN ('RESERVED','RUNNING','RETRY_REQUIRED','RECOVERY_REQUIRED'))
            OR EXISTS (SELECT 1 FROM publication_staging_closes WHERE job_id=NEW.job_id)) THEN
            RAISE EXCEPTION 'Material belongs to another operation or the staging job is closed';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER staging_owner_guard BEFORE INSERT OR UPDATE ON publication_staging_owners "
        "FOR EACH ROW EXECUTE FUNCTION staging_owner_guard()")
    op.execute("""CREATE FUNCTION staging_close_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM 1 FROM pbr_materials WHERE id IN (SELECT material_id FROM publication_staging_items WHERE job_id=NEW.job_id)
            ORDER BY id FOR UPDATE;
        PERFORM 1 FROM publication_staging_jobs WHERE id=NEW.job_id FOR UPDATE;
        IF NOT EXISTS (SELECT 1 FROM publication_staging_owners WHERE job_id=NEW.job_id AND active)
            OR EXISTS (SELECT 1 FROM publication_staging_owners WHERE job_id=NEW.job_id AND NOT active) THEN
            RAISE EXCEPTION 'Only an entirely active unsent staging reservation can close';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER staging_close_guard BEFORE INSERT ON publication_staging_closes "
        "FOR EACH ROW EXECUTE FUNCTION staging_close_guard()")
    op.execute("""CREATE FUNCTION staging_ownership_complete() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE target_job uuid; expected integer; closure uuid;
    BEGIN
        IF TG_TABLE_NAME='publication_staging_jobs' THEN target_job := NEW.id;
        ELSE target_job := NEW.job_id; END IF;
        SELECT material_count INTO expected FROM publication_staging_jobs WHERE id=target_job;
        SELECT id INTO closure FROM publication_staging_closes WHERE job_id=target_job;
        IF expected IS NULL OR (SELECT count(*) FROM publication_staging_items WHERE job_id=target_job)<>expected
            OR (SELECT count(*) FROM publication_staging_owners WHERE job_id=target_job)<>expected
            OR EXISTS (SELECT 1 FROM publication_staging_owners WHERE job_id=target_job
                AND (active IS DISTINCT FROM (closure IS NULL) OR close_id IS DISTINCT FROM closure)) THEN
            RAISE EXCEPTION 'Staging reservation requires complete consistent durable ownership';
        END IF;
        RETURN NULL;
    END; $$""")
    for table in ("publication_staging_jobs", "publication_staging_items", "publication_staging_closes", "publication_staging_owners"):
        operation = "INSERT OR UPDATE" if table == "publication_staging_owners" else "INSERT"
        op.execute(f"CREATE CONSTRAINT TRIGGER staging_ownership_complete AFTER {operation} ON {table} DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION staging_ownership_complete()")
    op.execute("""CREATE FUNCTION identity_staging_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.status IN ('RUNNING','RECOVERY_REQUIRED') THEN
            PERFORM 1 FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
            IF EXISTS (SELECT 1 FROM publication_staging_owners WHERE material_id=NEW.material_id AND active) THEN
                RAISE EXCEPTION 'Material already belongs to an active staging reservation';
            END IF;
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER identity_staging_guard BEFORE INSERT OR UPDATE OF material_id,status ON material_file_operations "
        "FOR EACH ROW EXECUTE FUNCTION identity_staging_guard()")
    op.execute("""CREATE FUNCTION packaging_staging_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.status IN ('RESERVED','RUNNING','RETRY_REQUIRED','RECOVERY_REQUIRED') THEN
            PERFORM 1 FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
            IF EXISTS (SELECT 1 FROM publication_staging_owners WHERE material_id=NEW.material_id AND active) THEN
                RAISE EXCEPTION 'Material already belongs to an active staging reservation';
            END IF;
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER packaging_staging_guard BEFORE INSERT OR UPDATE OF material_id,status ON material_packaging_states "
        "FOR EACH ROW EXECUTE FUNCTION packaging_staging_guard()")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM publication_staging_jobs) THEN "
        "RAISE EXCEPTION 'Staging reservation provenance exists; use a forward migration'; END IF; END $$")
    op.execute("DROP TRIGGER packaging_staging_guard ON material_packaging_states")
    op.execute("DROP TRIGGER identity_staging_guard ON material_file_operations")
    for table in ("publication_staging_owners", "publication_staging_items", "publication_staging_closes", "publication_staging_jobs"):
        op.drop_table(table)
    for name in ("packaging_staging_guard", "identity_staging_guard", "staging_ownership_complete", "staging_close_guard",
                 "staging_owner_guard", "staging_item_guard", "staging_job_guard"):
        op.execute(f"DROP FUNCTION {name}()")
