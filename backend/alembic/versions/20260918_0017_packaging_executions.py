"""Durable packaging inputs, dispatch facts and cross-operation material ownership."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260918_0017"
down_revision = "20260918_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('material_packaging_executions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('material_id', sa.Uuid(), nullable=False),
    sa.Column('batch_id', sa.Uuid(), nullable=False),
    sa.Column('brand_id', sa.Uuid(), nullable=False),
    sa.Column('policy_id', sa.Uuid(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=False),
    sa.Column('issuer_session_id', sa.Uuid(), nullable=False),
    sa.Column('request_key', sa.Uuid(), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('folder_path', sa.String(length=2048), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('input_snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('worker_request', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('worker_request_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("input_hash IS NULL OR (length(input_hash) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(input_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_packaging_executions_input_hash'),
    sa.CheckConstraint("request_hash IS NULL OR (length(request_hash) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(request_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_packaging_executions_request_hash'),
    sa.CheckConstraint("worker_request_hash IS NULL OR (length(worker_request_hash) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(worker_request_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_packaging_executions_worker_request_hash'),
    sa.CheckConstraint('length(folder_path) BETWEEN 1 AND 2048', name='ck_packaging_executions_folder'),
    sa.CheckConstraint('length(reason) BETWEEN 1 AND 2000', name='ck_packaging_executions_reason'),
    sa.ForeignKeyConstraint(['actor_id'], ['internal_users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['batch_id', 'material_id'], ['publication_batch_items.batch_id', 'publication_batch_items.material_id'], name='fk_packaging_executions_batch_item', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['brand_id'], ['published_brands.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['material_id', 'policy_id'], ['material_packaging_policies.material_id', 'material_packaging_policies.id'], name='fk_packaging_executions_policy', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['material_id'], ['pbr_materials.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('actor_id', 'request_key', name='uq_packaging_executions_request'),
    sa.UniqueConstraint('material_id', 'id', name='uq_packaging_executions_material')
    )
    op.create_index(op.f('ix_material_packaging_executions_brand_id'), 'material_packaging_executions', ['brand_id'], unique=False)
    op.create_index(op.f('ix_material_packaging_executions_created_at'), 'material_packaging_executions', ['created_at'], unique=False)
    op.create_index(op.f('ix_material_packaging_executions_material_id'), 'material_packaging_executions', ['material_id'], unique=False)
    op.create_table('material_packaging_dispatches',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('execution_id', sa.Uuid(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=False),
    sa.Column('issuer_session_id', sa.Uuid(), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('request_key', sa.Uuid(), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("action IN ('EXECUTE', 'RETRY', 'RECONCILE', 'CLOSE')", name='ck_packaging_dispatches_action'),
    sa.CheckConstraint("request_hash IS NULL OR (length(request_hash) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(request_hash, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_packaging_dispatches_request_hash'),
    sa.CheckConstraint('length(reason) BETWEEN 1 AND 2000', name='ck_packaging_dispatches_reason'),
    sa.CheckConstraint('ordinal >= 1', name='ck_packaging_dispatches_ordinal'),
    sa.ForeignKeyConstraint(['actor_id'], ['internal_users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['execution_id'], ['material_packaging_executions.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('actor_id', 'request_key', name='uq_packaging_dispatches_request'),
    sa.UniqueConstraint('execution_id', 'id', name='uq_packaging_dispatches_execution'),
    sa.UniqueConstraint('execution_id', 'ordinal', name='uq_packaging_dispatches_ordinal')
    )
    op.create_table('material_packaging_observations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('execution_id', sa.Uuid(), nullable=False),
    sa.Column('dispatch_id', sa.Uuid(), nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('worker_result', sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('failure_code', sa.String(length=100), nullable=True),
    sa.Column('proof_sha256', sa.String(length=64), nullable=True),
    sa.Column('inputs_current', sa.Boolean(), nullable=False),
    sa.Column('actor_current', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(outcome = 'READY' AND proof_sha256 IS NOT NULL) OR (outcome <> 'READY' AND proof_sha256 IS NULL)", name='ck_packaging_observations_proof'),
    sa.CheckConstraint("(outcome = 'UNCERTAIN' AND failure_code IS NOT NULL AND worker_result IS NULL) OR (outcome IN ('READY', 'RETRY_REQUIRED') AND failure_code IS NULL AND worker_result IS NOT NULL) OR (outcome = 'NOT_STARTED' AND failure_code IS NULL AND worker_result IS NULL AND NOT inputs_current AND NOT actor_current)", name='ck_packaging_observations_result'),
    sa.CheckConstraint("outcome IN ('READY', 'RETRY_REQUIRED', 'UNCERTAIN', 'NOT_STARTED')", name='ck_packaging_observations_outcome'),
    sa.CheckConstraint("proof_sha256 IS NULL OR (length(proof_sha256) = 64 AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(proof_sha256, '0', ''), '1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')", name='ck_packaging_observations_proof_sha256'),
    sa.ForeignKeyConstraint(['execution_id', 'dispatch_id'], ['material_packaging_dispatches.execution_id', 'material_packaging_dispatches.id'], name='fk_packaging_observations_dispatch', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['execution_id'], ['material_packaging_executions.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dispatch_id', name='uq_packaging_observations_dispatch'),
    sa.UniqueConstraint('execution_id', 'id', name='uq_packaging_observations_execution')
    )
    op.create_table('material_packaging_states',
    sa.Column('execution_id', sa.Uuid(), nullable=False),
    sa.Column('material_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('last_dispatch_id', sa.Uuid(), nullable=True),
    sa.Column('last_observation_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'RESERVED' AND last_dispatch_id IS NULL AND last_observation_id IS NULL) OR (status = 'RUNNING' AND last_dispatch_id IS NOT NULL AND last_observation_id IS NULL) OR (status NOT IN ('RESERVED', 'RUNNING') AND last_dispatch_id IS NOT NULL AND last_observation_id IS NOT NULL)", name='ck_packaging_states_progress'),
    sa.CheckConstraint("status IN ('RESERVED', 'RUNNING', 'RETRY_REQUIRED', 'RECOVERY_REQUIRED', 'PACKAGED', 'REJECTED')", name='ck_packaging_states_status'),
    sa.ForeignKeyConstraint(['execution_id', 'last_dispatch_id'], ['material_packaging_dispatches.execution_id', 'material_packaging_dispatches.id'], name='fk_packaging_states_dispatch', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['execution_id', 'last_observation_id'], ['material_packaging_observations.execution_id', 'material_packaging_observations.id'], name='fk_packaging_states_observation', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['material_id', 'execution_id'], ['material_packaging_executions.material_id', 'material_packaging_executions.id'], name='fk_packaging_states_execution', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('execution_id')
    )
    op.create_index('uq_packaging_states_active', 'material_packaging_states', ['material_id'], unique=True, postgresql_where=sa.text("status IN ('RESERVED', 'RUNNING', 'RETRY_REQUIRED', 'RECOVERY_REQUIRED')"), sqlite_where=sa.text("status IN ('RESERVED', 'RUNNING', 'RETRY_REQUIRED', 'RECOVERY_REQUIRED')"))

    for table in ("material_packaging_executions", "material_packaging_dispatches", "material_packaging_observations"):
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    op.execute("CREATE TRIGGER material_packaging_states_preserve BEFORE DELETE OR TRUNCATE ON material_packaging_states "
        "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    op.execute("""CREATE FUNCTION packaging_inputs_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE material pbr_materials%ROWTYPE; policy material_packaging_policies%ROWTYPE; source publication_batch_items%ROWTYPE;
    BEGIN
        SELECT * INTO material FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
        SELECT * INTO policy FROM material_packaging_policies WHERE material_id=NEW.material_id ORDER BY revision DESC LIMIT 1;
        SELECT * INTO source FROM publication_batch_items WHERE batch_id=NEW.batch_id AND material_id=NEW.material_id;
        IF material.id IS NULL OR policy.id IS NULL OR source.batch_id IS NULL
            OR NEW.brand_id IS DISTINCT FROM material.published_brand_id OR NEW.folder_path IS DISTINCT FROM material.folder_path
            OR NEW.policy_id IS DISTINCT FROM policy.id OR NEW.input_snapshot IS DISTINCT FROM source.snapshot
            OR NEW.input_hash IS DISTINCT FROM source.snapshot_hash
            OR jsonb_typeof(NEW.worker_request) IS DISTINCT FROM 'object'
            OR length(NEW.worker_request::text) > 65536 OR length(NEW.input_snapshot::text) > 8388608
            OR NEW.worker_request->>'request_hash' IS DISTINCT FROM NEW.worker_request_hash
            OR NEW.worker_request->'request'->>'operation_id' IS DISTINCT FROM NEW.id::text
            OR NEW.worker_request->'request'->>'source_revision_hash' IS DISTINCT FROM NEW.input_snapshot->>'source_revision_hash'
            OR NEW.worker_request->'request'->>'technical_report_hash' IS DISTINCT FROM NEW.input_snapshot->>'technical_report_hash'
            OR NEW.worker_request->'request'->>'policy' IS DISTINCT FROM policy.policy
            OR NEW.worker_request->'request'->>'storage_timezone' IS DISTINCT FROM policy.storage_timezone
            OR jsonb_typeof(NEW.worker_request->'request'->'parts') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'Packaging inputs must bind the approved batch and current policy';
        END IF;
        IF array_to_string(ARRAY(SELECT jsonb_array_elements_text(NEW.worker_request->'request'->'parts')), '/')
                IS DISTINCT FROM NEW.folder_path THEN
            RAISE EXCEPTION 'Packaging inputs must bind the material folder';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER packaging_inputs_guard BEFORE INSERT ON material_packaging_executions "
        "FOR EACH ROW EXECUTE FUNCTION packaging_inputs_guard()")
    op.execute("""CREATE FUNCTION packaging_dispatch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE prior integer; current_status text;
    BEGIN
        PERFORM 1 FROM material_packaging_executions WHERE id=NEW.execution_id FOR UPDATE;
        SELECT coalesce(max(ordinal),0) INTO prior FROM material_packaging_dispatches WHERE execution_id=NEW.execution_id;
        SELECT status INTO current_status FROM material_packaging_states WHERE execution_id=NEW.execution_id;
        IF NEW.ordinal <> prior+1 OR current_status IS NULL OR current_status IN ('PACKAGED','REJECTED')
            OR (NEW.action='EXECUTE' AND (prior<>0 OR current_status<>'RESERVED'))
            OR (NEW.action='RETRY' AND current_status<>'RETRY_REQUIRED')
            OR (NEW.action='RECONCILE' AND prior=0) THEN
            RAISE EXCEPTION 'Packaging dispatch must extend current active execution';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER packaging_dispatch_guard BEFORE INSERT ON material_packaging_dispatches "
        "FOR EACH ROW EXECUTE FUNCTION packaging_dispatch_guard()")
    op.execute("""CREATE FUNCTION packaging_observation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE execution material_packaging_executions%ROWTYPE; dispatch material_packaging_dispatches%ROWTYPE;
    BEGIN
        SELECT * INTO execution FROM material_packaging_executions WHERE id=NEW.execution_id;
        SELECT * INTO dispatch FROM material_packaging_dispatches WHERE execution_id=NEW.execution_id AND id=NEW.dispatch_id;
        IF execution.id IS NULL OR dispatch.id IS NULL
            OR (NEW.failure_code IS NOT NULL AND NEW.failure_code !~ '^PACKAGING_[A-Z0-9_]{1,90}$')
            OR (NEW.outcome='NOT_STARTED' AND (dispatch.action<>'CLOSE' OR dispatch.ordinal<>1)) THEN
            RAISE EXCEPTION 'Invalid packaging observation binding';
        END IF;
        IF NEW.outcome IN ('READY','RETRY_REQUIRED') AND
            (jsonb_typeof(NEW.worker_result) IS DISTINCT FROM 'object' OR length(NEW.worker_result::text)>33619968
            OR NEW.worker_result->>'operation_id' IS DISTINCT FROM execution.id::text
            OR NEW.worker_result->>'request_hash' IS DISTINCT FROM execution.worker_request_hash
            OR NEW.worker_result->>'status' IS DISTINCT FROM NEW.outcome
            OR (NEW.outcome='READY' AND NEW.worker_result->'stored'->>'proof_sha256' IS DISTINCT FROM NEW.proof_sha256)
            OR (NEW.outcome='RETRY_REQUIRED' AND NEW.worker_result->'stored' IS DISTINCT FROM 'null'::jsonb)) THEN
            RAISE EXCEPTION 'Packaging observation must bind the frozen worker request';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER packaging_observation_guard BEFORE INSERT ON material_packaging_observations "
        "FOR EACH ROW EXECUTE FUNCTION packaging_observation_guard()")
    op.execute("""CREATE FUNCTION packaging_state_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE dispatch material_packaging_dispatches%ROWTYPE; observation material_packaging_observations%ROWTYPE;
        latest integer;
    BEGIN
        PERFORM 1 FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
        IF TG_OP='UPDATE' AND (NEW.execution_id IS DISTINCT FROM OLD.execution_id OR NEW.material_id IS DISTINCT FROM OLD.material_id
            OR NEW.created_at IS DISTINCT FROM OLD.created_at OR (OLD.status IN ('PACKAGED','REJECTED') AND NEW IS DISTINCT FROM OLD)
            OR (NEW.status='RESERVED' AND OLD.status<>'RESERVED')) THEN
            RAISE EXCEPTION 'Packaging ownership identity and terminal state are immutable';
        END IF;
        IF NEW.status IN ('RESERVED','RUNNING','RETRY_REQUIRED','RECOVERY_REQUIRED')
            AND EXISTS (SELECT 1 FROM material_file_operations WHERE material_id=NEW.material_id AND status IN ('RUNNING','RECOVERY_REQUIRED')) THEN
            RAISE EXCEPTION 'Material already belongs to an active identity operation';
        END IF;
        SELECT coalesce(max(ordinal),0) INTO latest FROM material_packaging_dispatches WHERE execution_id=NEW.execution_id;
        IF NEW.status='RESERVED' THEN
            IF latest<>0 THEN RAISE EXCEPTION 'Reserved packaging execution cannot have dispatch history'; END IF;
            RETURN NEW;
        END IF;
        SELECT * INTO dispatch FROM material_packaging_dispatches WHERE execution_id=NEW.execution_id AND id=NEW.last_dispatch_id;
        IF dispatch.id IS NULL OR dispatch.ordinal<>latest THEN
            RAISE EXCEPTION 'Packaging state must refer to its latest dispatch';
        END IF;
        IF NEW.status='RUNNING' THEN
            IF EXISTS (SELECT 1 FROM material_packaging_observations WHERE dispatch_id=dispatch.id) THEN
                RAISE EXCEPTION 'Observed packaging dispatch cannot return to running';
            END IF;
            RETURN NEW;
        END IF;
        SELECT * INTO observation FROM material_packaging_observations WHERE execution_id=NEW.execution_id AND id=NEW.last_observation_id;
        IF observation.id IS NULL OR observation.dispatch_id<>dispatch.id
            OR (NEW.status='PACKAGED' AND (observation.outcome<>'READY' OR NOT observation.inputs_current OR NOT observation.actor_current))
            OR (NEW.status='RETRY_REQUIRED' AND observation.outcome<>'RETRY_REQUIRED')
            OR (NEW.status='REJECTED' AND (dispatch.action<>'CLOSE' OR observation.outcome NOT IN ('READY','RETRY_REQUIRED','NOT_STARTED')))
            OR (NEW.status='RECOVERY_REQUIRED' AND observation.outcome='NOT_STARTED') THEN
            RAISE EXCEPTION 'Packaging state is inconsistent with its observed outcome';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER packaging_state_guard BEFORE INSERT OR UPDATE ON material_packaging_states "
        "FOR EACH ROW EXECUTE FUNCTION packaging_state_guard()")
    op.execute("""CREATE FUNCTION identity_packaging_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.status IN ('RUNNING','RECOVERY_REQUIRED') THEN
            PERFORM 1 FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
            IF EXISTS (SELECT 1 FROM material_packaging_states WHERE material_id=NEW.material_id
                AND status IN ('RESERVED','RUNNING','RETRY_REQUIRED','RECOVERY_REQUIRED')) THEN
                RAISE EXCEPTION 'Material already belongs to an active packaging execution';
            END IF;
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER identity_packaging_guard BEFORE INSERT OR UPDATE OF material_id,status ON material_file_operations "
        "FOR EACH ROW EXECUTE FUNCTION identity_packaging_guard()")
    # An input and its ownership, or a dispatch and its state advance, must commit
    # atomically. A crash between statements must not leave an unclaimed execution.
    op.execute("""CREATE FUNCTION packaging_owner_required() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE target_execution_id uuid; owner material_packaging_states%ROWTYPE; latest uuid;
    BEGIN
        IF TG_TABLE_NAME='material_packaging_executions' THEN target_execution_id := NEW.id;
        ELSE target_execution_id := NEW.execution_id; END IF;
        SELECT * INTO owner FROM material_packaging_states WHERE material_packaging_states.execution_id=target_execution_id;
        SELECT id INTO latest FROM material_packaging_dispatches WHERE material_packaging_dispatches.execution_id=target_execution_id ORDER BY ordinal DESC LIMIT 1;
        IF owner.execution_id IS NULL OR owner.last_dispatch_id IS DISTINCT FROM latest THEN
            RAISE EXCEPTION 'Packaging execution and dispatch require matching durable ownership';
        END IF;
        RETURN NULL;
    END; $$""")
    for table in ("material_packaging_executions", "material_packaging_dispatches"):
        op.execute(f"CREATE CONSTRAINT TRIGGER packaging_owner_required AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION packaging_owner_required()")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM material_packaging_executions) THEN "
        "RAISE EXCEPTION 'Packaging execution provenance exists; preserve schema and use a forward migration'; END IF; END $$")
    op.execute("DROP TRIGGER identity_packaging_guard ON material_file_operations")
    for table in ("material_packaging_states", "material_packaging_observations", "material_packaging_dispatches", "material_packaging_executions"):
        op.drop_table(table)
    for function in ("packaging_owner_required", "identity_packaging_guard", "packaging_state_guard",
            "packaging_observation_guard", "packaging_dispatch_guard", "packaging_inputs_guard"):
        op.execute(f"DROP FUNCTION {function}()")
