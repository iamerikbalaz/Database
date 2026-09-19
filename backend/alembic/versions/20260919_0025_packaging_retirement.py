"""Append-only local-copy retirement intent, dispatch and observed receipts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260919_0025"
down_revision = "20260918_0024"
branch_labels = None
depends_on = None

INTENTS = "material_packaging_retirements"
DISPATCHES = "material_packaging_retirement_dispatches"
OBSERVATIONS = "material_packaging_retirement_observations"


def _hash(field, prefix):
    return sa.CheckConstraint(f"{field} ~ '^[a-f0-9]{{64}}$'", name=prefix + field)


def _actor_columns():
    return [sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("issuer_session_id", sa.Uuid(), nullable=False), sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False), sa.Column("reason", sa.Text(), nullable=False)]


def upgrade():
    op.create_table(INTENTS,
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False), sa.Column("accepted_observation_id", sa.Uuid(), nullable=False),
        *_actor_columns(),
        *[sa.Column(field, sa.String(64), nullable=False) for field in ("worker_request_hash", "plan_hash", "proof_sha256", "worker_command_hash")],
        sa.Column("file_count", sa.Integer(), nullable=False), sa.Column("byte_count", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("execution_id", name="uq_packaging_retirements_execution"),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_packaging_retirements_request"),
        sa.UniqueConstraint("id", "worker_command_hash", name="uq_packaging_retirements_command"),
        sa.ForeignKeyConstraint(["material_id", "execution_id"],
            ["material_packaging_executions.material_id", "material_packaging_executions.id"],
            name="fk_packaging_retirements_execution", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["execution_id", "accepted_observation_id"],
            ["material_packaging_observations.execution_id", "material_packaging_observations.id"],
            name="fk_packaging_retirements_observation", ondelete="RESTRICT"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_packaging_retirements_reason"),
        sa.CheckConstraint("file_count BETWEEN 2 AND 20008", name="ck_packaging_retirements_files"),
        sa.CheckConstraint("byte_count BETWEEN 0 AND 137438953472", name="ck_packaging_retirements_bytes"),
        *[_hash(field, "ck_packaging_retirements_") for field in ("request_hash", "worker_request_hash", "plan_hash", "proof_sha256", "worker_command_hash")])
    op.create_index("ix_material_packaging_retirements_material_id", INTENTS, ["material_id"])
    op.create_index("ix_material_packaging_retirements_created_at", INTENTS, ["created_at"])
    op.create_table(DISPATCHES,
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("retirement_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False), sa.Column("previous_dispatch_id", sa.Uuid()),
        sa.Column("action", sa.String(16), nullable=False), *_actor_columns(),
        sa.Column("worker_command_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("retirement_id", "id", name="uq_retirement_dispatches_retirement"),
        sa.UniqueConstraint("retirement_id", "ordinal", name="uq_retirement_dispatches_order"),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_retirement_dispatches_request"),
        sa.ForeignKeyConstraint(["retirement_id", "worker_command_hash"],
            [INTENTS + ".id", INTENTS + ".worker_command_hash"], name="fk_retirement_dispatches_command", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["retirement_id", "previous_dispatch_id"],
            [DISPATCHES + ".retirement_id", DISPATCHES + ".id"], name="fk_retirement_dispatches_previous", ondelete="RESTRICT"),
        sa.CheckConstraint("ordinal BETWEEN 1 AND 2147483647", name="ck_retirement_dispatches_order"),
        sa.CheckConstraint("(ordinal=1 AND previous_dispatch_id IS NULL AND action='EXECUTE') OR "
            "(ordinal>1 AND previous_dispatch_id IS NOT NULL AND action='RECONCILE')", name="ck_retirement_dispatches_sequence"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_retirement_dispatches_reason"),
        *[_hash(field, "ck_retirement_dispatches_") for field in ("request_hash", "worker_command_hash")])
    op.create_table(OBSERVATIONS,
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("retirement_id", sa.Uuid(), nullable=False),
        sa.Column("dispatch_id", sa.Uuid(), nullable=False), sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("receipt", postgresql.JSONB(none_as_null=True)), sa.Column("failure_code", sa.String(100)),
        sa.Column("actor_current", sa.Boolean(), nullable=False), sa.Column("lease_current", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dispatch_id", name="uq_retirement_observations_dispatch"),
        sa.ForeignKeyConstraint(["retirement_id", "dispatch_id"], [DISPATCHES + ".retirement_id", DISPATCHES + ".id"],
            name="fk_retirement_observations_dispatch", ondelete="RESTRICT"),
        sa.CheckConstraint("(outcome='REMOVED' AND receipt IS NOT NULL AND failure_code IS NULL) OR "
            "(outcome='UNCERTAIN' AND receipt IS NULL AND failure_code IS NOT NULL)", name="ck_retirement_observations_result"),
        sa.CheckConstraint("failure_code IS NULL OR length(failure_code) BETWEEN 1 AND 100", name="ck_retirement_observations_failure"))
    op.create_index("ix_material_packaging_retirement_observations_retirement_id", OBSERVATIONS, ["retirement_id"])
    for table in (INTENTS, DISPATCHES, OBSERVATIONS):
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")

    op.execute("""CREATE FUNCTION packaging_retirement_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE execution material_packaging_executions%ROWTYPE; state material_packaging_states%ROWTYPE;
        observed material_packaging_observations%ROWTYPE; files jsonb; bytes bigint;
    BEGIN
        PERFORM 1 FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
        SELECT * INTO execution FROM material_packaging_executions WHERE id=NEW.execution_id;
        SELECT * INTO state FROM material_packaging_states WHERE execution_id=NEW.execution_id FOR UPDATE;
        SELECT * INTO observed FROM material_packaging_observations WHERE id=NEW.accepted_observation_id;
        IF execution.id IS NULL OR state.execution_id IS NULL OR observed.id IS NULL
            OR execution.material_id IS DISTINCT FROM NEW.material_id OR state.status IS DISTINCT FROM 'PACKAGED'
            OR state.last_observation_id IS DISTINCT FROM observed.id OR state.last_dispatch_id IS DISTINCT FROM observed.dispatch_id
            OR observed.execution_id IS DISTINCT FROM execution.id OR observed.outcome IS DISTINCT FROM 'READY'
            OR NOT observed.inputs_current OR NOT observed.actor_current
            OR NEW.worker_request_hash IS DISTINCT FROM execution.worker_request_hash
            OR NEW.plan_hash IS DISTINCT FROM execution.worker_request->'request'->>'plan_hash'
            OR NEW.proof_sha256 IS DISTINCT FROM observed.proof_sha256
            OR NEW.proof_sha256 IS DISTINCT FROM observed.worker_result->'stored'->>'proof_sha256'
            OR EXISTS (SELECT 1 FROM publication_staging_items item
                JOIN publication_staging_owners owner USING (job_id, material_id)
                WHERE item.execution_id=NEW.execution_id AND owner.active) THEN
            RAISE EXCEPTION 'Retirement requires an accepted copy without an active staging owner';
        END IF;
        files := observed.worker_result->'stored'->'payload'->'files';
        IF jsonb_typeof(files) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'Retirement requires an accepted manifest'; END IF;
        SELECT sum((value->>'size')::bigint) INTO bytes FROM jsonb_array_elements(files);
        IF NEW.file_count IS DISTINCT FROM jsonb_array_length(files) OR NEW.byte_count IS DISTINCT FROM bytes THEN
            RAISE EXCEPTION 'Retirement must bind the accepted byte and file totals';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute(f"CREATE TRIGGER packaging_retirement_guard BEFORE INSERT ON {INTENTS} "
        "FOR EACH ROW EXECUTE FUNCTION packaging_retirement_guard()")

    # Both item insertion and later ownership claims serialize on the same
    # material row as retirement. Existing immutable staging evidence remains.
    op.execute("""CREATE FUNCTION staging_retirement_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE selected_execution uuid;
    BEGIN
        PERFORM 1 FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
        IF TG_TABLE_NAME='publication_staging_items' THEN
            selected_execution := NEW.execution_id;
        ELSIF NEW.active THEN
            SELECT execution_id INTO selected_execution FROM publication_staging_items
                WHERE job_id=NEW.job_id AND material_id=NEW.material_id;
        END IF;
        IF selected_execution IS NOT NULL AND EXISTS (SELECT 1 FROM material_packaging_retirements WHERE execution_id=selected_execution) THEN
            RAISE EXCEPTION 'A retired local copy cannot be selected for staging';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER staging_item_retirement_guard BEFORE INSERT ON publication_staging_items "
        "FOR EACH ROW EXECUTE FUNCTION staging_retirement_guard()")
    op.execute("CREATE TRIGGER staging_owner_retirement_guard BEFORE INSERT OR UPDATE ON publication_staging_owners "
        "FOR EACH ROW EXECUTE FUNCTION staging_retirement_guard()")

    op.execute("""CREATE FUNCTION retirement_dispatch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE previous material_packaging_retirement_dispatches%ROWTYPE;
    BEGIN
        PERFORM 1 FROM material_packaging_retirements WHERE id=NEW.retirement_id FOR UPDATE;
        SELECT * INTO previous FROM material_packaging_retirement_dispatches WHERE retirement_id=NEW.retirement_id ORDER BY ordinal DESC LIMIT 1;
        IF NEW.ordinal IS DISTINCT FROM coalesce(previous.ordinal,0)+1 OR NEW.previous_dispatch_id IS DISTINCT FROM previous.id
            OR EXISTS (SELECT 1 FROM material_packaging_retirement_observations WHERE retirement_id=NEW.retirement_id AND outcome='REMOVED') THEN
            RAISE EXCEPTION 'Retirement dispatch must extend the unfinished command history';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute(f"CREATE TRIGGER retirement_dispatch_guard BEFORE INSERT ON {DISPATCHES} "
        "FOR EACH ROW EXECUTE FUNCTION retirement_dispatch_guard()")
    op.execute("""CREATE FUNCTION retirement_observation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE intent material_packaging_retirements%ROWTYPE; expected jsonb;
    BEGIN
        SELECT * INTO intent FROM material_packaging_retirements WHERE id=NEW.retirement_id FOR UPDATE;
        IF NEW.outcome='REMOVED' THEN
            expected := jsonb_build_object('schema_version',1,'status','REMOVED','operation_id',intent.execution_id::text,
                'request_hash',intent.worker_request_hash,'plan_hash',intent.plan_hash,'proof_sha256',intent.proof_sha256,
                'retirement_id',intent.id::text,'retirement_request_hash',intent.worker_command_hash,
                'file_count',intent.file_count,'byte_count',intent.byte_count);
            IF intent.id IS NULL OR NEW.receipt IS DISTINCT FROM expected OR octet_length(NEW.receipt::text)>4096 THEN
                RAISE EXCEPTION 'Retirement receipt must bind every immutable intent field';
            END IF;
        ELSIF NEW.failure_code !~ '^[A-Z][A-Z0-9_]{0,99}$' THEN
            RAISE EXCEPTION 'Retirement failure must use a fixed diagnostic code';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute(f"CREATE TRIGGER retirement_observation_guard BEFORE INSERT ON {OBSERVATIONS} "
        "FOR EACH ROW EXECUTE FUNCTION retirement_observation_guard()")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM material_packaging_retirements)
        OR EXISTS (SELECT 1 FROM material_packaging_retirement_dispatches)
        OR EXISTS (SELECT 1 FROM material_packaging_retirement_observations) THEN
        RAISE EXCEPTION 'Retirement evidence exists; preserve it and use a forward migration';
        END IF; END $$""")
    op.execute("DROP TRIGGER staging_item_retirement_guard ON publication_staging_items")
    op.execute("DROP TRIGGER staging_owner_retirement_guard ON publication_staging_owners")
    op.drop_table(OBSERVATIONS); op.drop_table(DISPATCHES); op.drop_table(INTENTS)
    for name in ("retirement_observation_guard", "retirement_dispatch_guard", "staging_retirement_guard", "packaging_retirement_guard"):
        op.execute(f"DROP FUNCTION {name}()")
