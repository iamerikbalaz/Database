"""Durable staging dispatch intents, storage observations and guarded progress."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260918_0019"
down_revision = "20260918_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("publication_staging_closes", sa.Column("dispatched", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.create_table('publication_staging_dispatches',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('previous_dispatch_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=False),
    sa.Column('issuer_session_id', sa.Uuid(), nullable=False),
    sa.Column('request_key', sa.Uuid(), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('plan_sha256', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(ordinal=1 AND previous_dispatch_id IS NULL AND action='EXECUTE') OR (ordinal>1 AND previous_dispatch_id IS NOT NULL AND action='RECONCILE')", name='ck_staging_dispatches_sequence'),
    sa.CheckConstraint("action IN ('EXECUTE','RECONCILE')", name='ck_staging_dispatches_action'),
    sa.CheckConstraint("plan_sha256 IS NULL OR (length(plan_sha256) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(plan_sha256, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_staging_dispatches_plan_sha256'),
    sa.CheckConstraint("request_hash IS NULL OR (length(request_hash) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(request_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_staging_dispatches_request_hash'),
    sa.CheckConstraint('length(reason) BETWEEN 1 AND 2000', name='ck_staging_dispatches_reason'),
    sa.CheckConstraint('ordinal BETWEEN 1 AND 2147483647', name='ck_staging_dispatches_ordinal'),
    sa.ForeignKeyConstraint(['actor_id'], ['internal_users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id', 'previous_dispatch_id'], ['publication_staging_dispatches.job_id', 'publication_staging_dispatches.id'], name='fk_staging_dispatches_previous', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id'], ['publication_staging_jobs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('actor_id', 'request_key', name='uq_staging_dispatches_request'),
    sa.UniqueConstraint('job_id', 'id', name='uq_staging_dispatches_job'),
    sa.UniqueConstraint('job_id', 'ordinal', name='uq_staging_dispatches_order')
    )
    op.create_table('publication_staging_transfers',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('dispatch_id', sa.Uuid(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('relative_path', sa.String(length=900), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(kind='MARKER' AND relative_path='_reawote/complete.json' AND size<=33554432) OR (kind='DATA' AND relative_path<>'_reawote/complete.json')", name='ck_staging_transfers_marker'),
    sa.CheckConstraint("kind IN ('DATA','MARKER')", name='ck_staging_transfers_kind'),
    sa.CheckConstraint("sha256 IS NULL OR (length(sha256) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(sha256, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_staging_transfers_sha256'),
    sa.CheckConstraint('length(relative_path) BETWEEN 1 AND 900', name='ck_staging_transfers_path'),
    sa.CheckConstraint('ordinal BETWEEN 1 AND 20002', name='ck_staging_transfers_ordinal'),
    sa.CheckConstraint('size BETWEEN 1 AND 17179869184', name='ck_staging_transfers_size'),
    sa.ForeignKeyConstraint(['job_id', 'dispatch_id'], ['publication_staging_dispatches.job_id', 'publication_staging_dispatches.id'], name='fk_staging_transfers_dispatch', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dispatch_id', 'ordinal', name='uq_staging_transfers_order'),
    sa.UniqueConstraint('dispatch_id', 'relative_path', name='uq_staging_transfers_object'),
    sa.UniqueConstraint('job_id', 'dispatch_id', 'id', name='uq_staging_transfers_binding')
    )
    op.create_table('publication_staging_observations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('dispatch_id', sa.Uuid(), nullable=False),
    sa.Column('transfer_id', sa.Uuid(), nullable=False),
    sa.Column('outcome', sa.String(length=16), nullable=False),
    sa.Column('receipt', sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('failure_code', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(outcome='VERIFIED' AND receipt IS NOT NULL AND failure_code IS NULL) OR (outcome='UNCERTAIN' AND receipt IS NULL AND failure_code IS NOT NULL)", name='ck_staging_observations_result'),
    sa.CheckConstraint("outcome IN ('VERIFIED','UNCERTAIN')", name='ck_staging_observations_outcome'),
    sa.CheckConstraint('failure_code IS NULL OR length(failure_code) BETWEEN 1 AND 64', name='ck_staging_observations_failure'),
    sa.ForeignKeyConstraint(['job_id', 'dispatch_id', 'transfer_id'], ['publication_staging_transfers.job_id', 'publication_staging_transfers.dispatch_id', 'publication_staging_transfers.id'], name='fk_staging_observations_transfer', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'dispatch_id', 'id', name='uq_staging_observations_binding'),
    sa.UniqueConstraint('transfer_id', name='uq_staging_observations_transfer')
    )
    op.create_table('publication_staging_results',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('dispatch_id', sa.Uuid(), nullable=False),
    sa.Column('outcome', sa.String(length=16), nullable=False),
    sa.Column('completion_observation_id', sa.Uuid(), nullable=True),
    sa.Column('failure_code', sa.String(length=64), nullable=True),
    sa.Column('inputs_current', sa.Boolean(), nullable=False),
    sa.Column('actor_current', sa.Boolean(), nullable=False),
    sa.Column('lease_current', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(outcome='VERIFIED' AND completion_observation_id IS NOT NULL AND failure_code IS NULL) OR (outcome='UNCERTAIN' AND completion_observation_id IS NULL AND failure_code IS NOT NULL)", name='ck_staging_results_result'),
    sa.CheckConstraint("outcome IN ('VERIFIED','UNCERTAIN')", name='ck_staging_results_outcome'),
    sa.CheckConstraint('failure_code IS NULL OR length(failure_code) BETWEEN 1 AND 64', name='ck_staging_results_failure'),
    sa.ForeignKeyConstraint(['job_id', 'dispatch_id', 'completion_observation_id'], ['publication_staging_observations.job_id', 'publication_staging_observations.dispatch_id', 'publication_staging_observations.id'], name='fk_staging_results_completion', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id', 'dispatch_id'], ['publication_staging_dispatches.job_id', 'publication_staging_dispatches.id'], name='fk_staging_results_dispatch', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dispatch_id', name='uq_staging_results_dispatch'),
    sa.UniqueConstraint('job_id', 'dispatch_id', 'id', name='uq_staging_results_binding')
    )
    op.create_table('publication_staging_states',
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('last_dispatch_id', sa.Uuid(), nullable=True),
    sa.Column('last_result_id', sa.Uuid(), nullable=True),
    sa.Column('close_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status='CLOSED' AND close_id IS NOT NULL) OR (status<>'CLOSED' AND close_id IS NULL)", name='ck_staging_states_close'),
    sa.CheckConstraint("(status='RESERVED' AND last_dispatch_id IS NULL AND last_result_id IS NULL) OR (status='RUNNING' AND last_dispatch_id IS NOT NULL AND last_result_id IS NULL) OR (status IN ('RECOVERY_REQUIRED','STAGED_VERIFIED') AND last_dispatch_id IS NOT NULL AND last_result_id IS NOT NULL) OR status='CLOSED'", name='ck_staging_states_progress'),
    sa.CheckConstraint("status IN ('RESERVED','RUNNING','RECOVERY_REQUIRED','STAGED_VERIFIED','CLOSED')", name='ck_staging_states_status'),
    sa.ForeignKeyConstraint(['job_id', 'close_id'], ['publication_staging_closes.job_id', 'publication_staging_closes.id'], name='fk_staging_states_close', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id', 'last_dispatch_id', 'last_result_id'], ['publication_staging_results.job_id', 'publication_staging_results.dispatch_id', 'publication_staging_results.id'], name='fk_staging_states_result', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id', 'last_dispatch_id'], ['publication_staging_dispatches.job_id', 'publication_staging_dispatches.id'], name='fk_staging_states_dispatch', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id'], ['publication_staging_jobs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('job_id')
    )
    op.execute("""INSERT INTO publication_staging_states(job_id,status,close_id)
        SELECT job.id, CASE WHEN closed.id IS NULL THEN 'RESERVED' ELSE 'CLOSED' END, closed.id
        FROM publication_staging_jobs job LEFT JOIN publication_staging_closes closed ON closed.job_id=job.id""")
    _guards()

def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM publication_staging_dispatches) THEN "
        "RAISE EXCEPTION 'Staging dispatch provenance exists; use a forward migration'; END IF; END $$")
    for table in ("publication_staging_jobs", "publication_staging_closes"):
        op.execute(f"DROP TRIGGER staging_progress_complete ON {table}")
    # Restore the exact pre-dispatch closure semantics before removing new tables.
    op.execute("""CREATE OR REPLACE FUNCTION staging_close_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
    for table in ("publication_staging_states", "publication_staging_results", "publication_staging_observations",
                  "publication_staging_transfers", "publication_staging_dispatches"):
        op.drop_table(table)
    for name in ("staging_progress_complete", "staging_state_guard", "staging_result_guard",
                 "staging_storage_observation_guard", "staging_transfer_guard", "staging_dispatch_guard"):
        op.execute(f"DROP FUNCTION {name}()")
    op.drop_column("publication_staging_closes", "dispatched")


def _guards():
    for table in ("publication_staging_dispatches", "publication_staging_transfers", "publication_staging_observations", "publication_staging_results"):
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    op.execute("CREATE TRIGGER publication_staging_states_preserve BEFORE DELETE OR TRUNCATE ON publication_staging_states "
        "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    op.execute("""CREATE FUNCTION staging_dispatch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE job publication_staging_jobs%ROWTYPE; state publication_staging_states%ROWTYPE;
        prior publication_staging_dispatches%ROWTYPE;
    BEGIN
        SELECT * INTO job FROM publication_staging_jobs WHERE id=NEW.job_id FOR UPDATE;
        SELECT * INTO state FROM publication_staging_states WHERE job_id=NEW.job_id;
        SELECT * INTO prior FROM publication_staging_dispatches WHERE job_id=NEW.job_id ORDER BY ordinal DESC LIMIT 1;
        IF job.id IS NULL OR state.job_id IS NULL OR state.status='CLOSED'
            OR EXISTS (SELECT 1 FROM publication_staging_closes WHERE job_id=NEW.job_id)
            OR (SELECT count(*) FROM publication_staging_owners WHERE job_id=NEW.job_id AND active)<>job.material_count
            OR NEW.plan_sha256 IS DISTINCT FROM job.plan_sha256
            OR NEW.ordinal IS DISTINCT FROM coalesce(prior.ordinal,0)+1
            OR NEW.previous_dispatch_id IS DISTINCT FROM prior.id
            OR state.last_dispatch_id IS DISTINCT FROM prior.id THEN
            RAISE EXCEPTION 'Staging dispatch must extend the current active job';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER staging_dispatch_guard BEFORE INSERT ON publication_staging_dispatches "
        "FOR EACH ROW EXECUTE FUNCTION staging_dispatch_guard()")
    op.execute("""CREATE FUNCTION staging_transfer_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE job publication_staging_jobs%ROWTYPE; state publication_staging_states%ROWTYPE;
        dispatch publication_staging_dispatches%ROWTYPE; planned jsonb; expected integer; prior integer;
    BEGIN
        SELECT * INTO job FROM publication_staging_jobs WHERE id=NEW.job_id FOR UPDATE;
        SELECT * INTO state FROM publication_staging_states WHERE job_id=NEW.job_id;
        SELECT * INTO dispatch FROM publication_staging_dispatches WHERE id=NEW.dispatch_id;
        expected := jsonb_array_length(job.plan->'body'->'objects');
        SELECT coalesce(max(ordinal),0) INTO prior FROM publication_staging_transfers WHERE dispatch_id=NEW.dispatch_id;
        IF job.id IS NULL OR state.job_id IS NULL OR dispatch.id IS NULL OR dispatch.job_id IS DISTINCT FROM NEW.job_id
            OR state.status IS DISTINCT FROM 'RUNNING' OR state.last_dispatch_id IS DISTINCT FROM NEW.dispatch_id
            OR EXISTS (SELECT 1 FROM publication_staging_closes WHERE job_id=NEW.job_id)
            OR EXISTS (SELECT 1 FROM publication_staging_results WHERE dispatch_id=NEW.dispatch_id)
            OR NEW.ordinal<>prior+1 OR EXISTS (
                SELECT 1 FROM publication_staging_transfers transfer
                LEFT JOIN publication_staging_observations observed ON observed.transfer_id=transfer.id
                WHERE transfer.dispatch_id=NEW.dispatch_id AND observed.outcome IS DISTINCT FROM 'VERIFIED') THEN
            RAISE EXCEPTION 'Staging transfer must follow verified current dispatch progress';
        END IF;
        IF NEW.kind='DATA' THEN
            planned := job.plan->'body'->'objects'->(NEW.ordinal-1);
            IF NEW.ordinal>expected OR planned IS NULL OR planned->>'relative_path' IS DISTINCT FROM NEW.relative_path
                OR planned->'size' IS DISTINCT FROM to_jsonb(NEW.size) OR planned->>'sha256' IS DISTINCT FROM NEW.sha256 THEN
                RAISE EXCEPTION 'Staging transfer must match the exact ordered plan object';
            END IF;
        ELSIF NEW.ordinal<>expected+1 OR prior<>expected THEN
            RAISE EXCEPTION 'Completion marker requires every planned object observation';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER staging_transfer_guard BEFORE INSERT ON publication_staging_transfers "
        "FOR EACH ROW EXECUTE FUNCTION staging_transfer_guard()")
    op.execute("""CREATE FUNCTION staging_storage_observation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE job publication_staging_jobs%ROWTYPE; transfer publication_staging_transfers%ROWTYPE; receipt jsonb; spec jsonb;
    BEGIN
        SELECT * INTO job FROM publication_staging_jobs WHERE id=NEW.job_id;
        SELECT * INTO transfer FROM publication_staging_transfers WHERE id=NEW.transfer_id;
        IF job.id IS NULL OR transfer.id IS NULL OR transfer.job_id IS DISTINCT FROM NEW.job_id
            OR transfer.dispatch_id IS DISTINCT FROM NEW.dispatch_id THEN
            RAISE EXCEPTION 'Storage observation must bind its immutable transfer intent';
        END IF;
        IF NEW.failure_code IS NOT NULL AND NEW.failure_code !~ '^GCS_[A-Z0-9_]{1,60}$' THEN
            RAISE EXCEPTION 'Storage observation must use a fixed failure code';
        END IF;
        IF NEW.outcome='VERIFIED' THEN
            receipt := NEW.receipt; spec := receipt->'spec';
            IF jsonb_typeof(receipt) IS DISTINCT FROM 'object' OR jsonb_typeof(spec) IS DISTINCT FROM 'object'
                OR length(receipt::text)>32768 OR receipt->>'bucket_name' IS DISTINCT FROM job.bucket_name
                OR receipt->>'object_name' IS DISTINCT FROM job.staging_prefix||'/'||job.id::text||'/'||transfer.relative_path
                OR spec->>'job_id' IS DISTINCT FROM job.id::text OR spec->>'binding_sha256' IS DISTINCT FROM job.plan_sha256
                OR spec->>'relative_path' IS DISTINCT FROM transfer.relative_path OR spec->'size' IS DISTINCT FROM to_jsonb(transfer.size)
                OR spec->>'sha256' IS DISTINCT FROM transfer.sha256
                OR jsonb_typeof(receipt->'bucket_name') IS DISTINCT FROM 'string'
                OR jsonb_typeof(receipt->'object_name') IS DISTINCT FROM 'string'
                OR jsonb_typeof(spec->'job_id') IS DISTINCT FROM 'string'
                OR jsonb_typeof(spec->'binding_sha256') IS DISTINCT FROM 'string'
                OR jsonb_typeof(spec->'relative_path') IS DISTINCT FROM 'string'
                OR jsonb_typeof(spec->'sha256') IS DISTINCT FROM 'string'
                OR jsonb_typeof(receipt->'generation') IS DISTINCT FROM 'string'
                OR jsonb_typeof(receipt->'metageneration') IS DISTINCT FROM 'string'
                OR receipt->>'generation' !~ '^[1-9][0-9]{0,18}$' OR receipt->>'metageneration' !~ '^[1-9][0-9]{0,18}$' THEN
                RAISE EXCEPTION 'Verified storage receipt does not match its exact object';
            END IF;
            IF (SELECT count(*) FROM jsonb_object_keys(receipt))<>5 OR (SELECT count(*) FROM jsonb_object_keys(spec))<>5
                OR (receipt->>'generation')::numeric>9223372036854775807
                OR (receipt->>'metageneration')::numeric>9223372036854775807 THEN
                RAISE EXCEPTION 'Invalid storage receipt structure or generation';
            END IF;
        END IF;
        -- A late factual observation remains append-only even after closure or a newer dispatch.
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER staging_storage_observation_guard BEFORE INSERT ON publication_staging_observations "
        "FOR EACH ROW EXECUTE FUNCTION staging_storage_observation_guard()")
    op.execute("""CREATE FUNCTION staging_result_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE job publication_staging_jobs%ROWTYPE; marker publication_staging_observations%ROWTYPE; expected integer;
    BEGIN
        SELECT * INTO job FROM publication_staging_jobs WHERE id=NEW.job_id FOR UPDATE;
        expected := jsonb_array_length(job.plan->'body'->'objects');
        IF NEW.failure_code IS NOT NULL AND NEW.failure_code !~ '^GCS_[A-Z0-9_]{1,60}$' THEN
            RAISE EXCEPTION 'Staging result must use a fixed failure code';
        END IF;
        IF NEW.outcome='VERIFIED' THEN
            SELECT * INTO marker FROM publication_staging_observations WHERE id=NEW.completion_observation_id;
            IF job.id IS NULL OR marker.id IS NULL OR marker.job_id IS DISTINCT FROM NEW.job_id
                OR marker.dispatch_id IS DISTINCT FROM NEW.dispatch_id OR marker.outcome IS DISTINCT FROM 'VERIFIED'
                OR NOT EXISTS (SELECT 1 FROM publication_staging_transfers WHERE id=marker.transfer_id AND kind='MARKER')
                OR (SELECT count(*) FROM publication_staging_transfers WHERE dispatch_id=NEW.dispatch_id)<>expected+1
                OR EXISTS (SELECT 1 FROM publication_staging_transfers transfer
                    LEFT JOIN publication_staging_observations observed ON observed.transfer_id=transfer.id
                    WHERE transfer.dispatch_id=NEW.dispatch_id AND observed.outcome IS DISTINCT FROM 'VERIFIED') THEN
                RAISE EXCEPTION 'Verified staging result requires exact complete receipt coverage and marker';
            END IF;
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER staging_result_guard BEFORE INSERT ON publication_staging_results "
        "FOR EACH ROW EXECUTE FUNCTION staging_result_guard()")
    op.execute("""CREATE FUNCTION staging_state_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM 1 FROM publication_staging_jobs WHERE id=NEW.job_id FOR UPDATE;
        IF TG_OP='UPDATE' AND (NEW.job_id IS DISTINCT FROM OLD.job_id OR NEW.created_at IS DISTINCT FROM OLD.created_at
            OR (NEW.status='CLOSED' AND (NEW.last_dispatch_id IS DISTINCT FROM OLD.last_dispatch_id
                OR NEW.last_result_id IS DISTINCT FROM OLD.last_result_id))
            OR (OLD.status='CLOSED' AND NEW IS DISTINCT FROM OLD)) THEN
            RAISE EXCEPTION 'Staging progress identity and closed state are immutable';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER staging_state_guard BEFORE INSERT OR UPDATE ON publication_staging_states "
        "FOR EACH ROW EXECUTE FUNCTION staging_state_guard()")
    _progress_guards()


def _progress_guards():
    op.execute("""CREATE OR REPLACE FUNCTION staging_close_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM 1 FROM pbr_materials WHERE id IN (SELECT material_id FROM publication_staging_items WHERE job_id=NEW.job_id)
            ORDER BY id FOR UPDATE;
        PERFORM 1 FROM publication_staging_jobs WHERE id=NEW.job_id FOR UPDATE;
        IF NOT EXISTS (SELECT 1 FROM publication_staging_owners WHERE job_id=NEW.job_id AND active)
            OR EXISTS (SELECT 1 FROM publication_staging_owners WHERE job_id=NEW.job_id AND NOT active)
            OR NEW.dispatched IS DISTINCT FROM (EXISTS (SELECT 1 FROM publication_staging_dispatches WHERE job_id=NEW.job_id)) THEN
            RAISE EXCEPTION 'Staging closure must acknowledge its exact dispatch history and active owners';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("""CREATE FUNCTION staging_progress_complete() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE target_job uuid; state publication_staging_states%ROWTYPE; latest publication_staging_dispatches%ROWTYPE;
        result publication_staging_results%ROWTYPE; closed publication_staging_closes%ROWTYPE; expected_status text;
    BEGIN
        IF TG_TABLE_NAME='publication_staging_jobs' THEN target_job := NEW.id;
        ELSE target_job := NEW.job_id; END IF;
        SELECT * INTO state FROM publication_staging_states WHERE job_id=target_job;
        SELECT * INTO latest FROM publication_staging_dispatches WHERE job_id=target_job ORDER BY ordinal DESC LIMIT 1;
        SELECT * INTO closed FROM publication_staging_closes WHERE job_id=target_job;
        IF state.job_id IS NULL OR state.last_dispatch_id IS DISTINCT FROM latest.id THEN
            RAISE EXCEPTION 'Staging job requires durable progress bound to its latest dispatch';
        END IF;
        IF closed.id IS NOT NULL THEN
            IF state.status IS DISTINCT FROM 'CLOSED' OR state.close_id IS DISTINCT FROM closed.id
                OR closed.dispatched IS DISTINCT FROM (latest.id IS NOT NULL) THEN
                RAISE EXCEPTION 'Closed staging progress must retain its exact immutable closure';
            END IF;
            -- Late observations/results do not rewrite the progress frozen at closure.
            RETURN NULL;
        END IF;
        SELECT * INTO result FROM publication_staging_results WHERE dispatch_id=latest.id;
        IF latest.id IS NULL THEN expected_status := 'RESERVED';
        ELSIF result.id IS NULL THEN expected_status := 'RUNNING';
        ELSIF result.outcome='VERIFIED' AND result.inputs_current AND result.actor_current AND result.lease_current THEN
            expected_status := 'STAGED_VERIFIED';
        ELSE expected_status := 'RECOVERY_REQUIRED'; END IF;
        IF state.status IS DISTINCT FROM expected_status OR state.close_id IS NOT NULL
            OR state.last_result_id IS DISTINCT FROM result.id THEN
            RAISE EXCEPTION 'Staging progress must match the current immutable result and acceptance checks';
        END IF;
        RETURN NULL;
    END; $$""")
    for table in ("publication_staging_jobs", "publication_staging_dispatches", "publication_staging_results",
                  "publication_staging_closes", "publication_staging_states"):
        operations = "INSERT OR UPDATE" if table == "publication_staging_states" else "INSERT"
        op.execute(f"CREATE CONSTRAINT TRIGGER staging_progress_complete AFTER {operations} ON {table} DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION staging_progress_complete()")
