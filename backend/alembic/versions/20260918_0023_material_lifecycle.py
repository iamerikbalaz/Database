"""Reversible material archive with transactional, immutable lifecycle evidence."""
from alembic import op
import sqlalchemy as sa

revision = "20260918_0023"
down_revision = "20260918_0022"
branch_labels = None
depends_on = None


def _hash(column, name):
    remainder = column
    for character in "0123456789abcdef": remainder = f"replace({remainder}, '{character}', '')"
    return sa.CheckConstraint(f"length({column}) = 64 AND {remainder} = ''", name=name)


def upgrade():
    op.create_table("material_lifecycle_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("review_generation", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("material_id", "version", name="uq_material_lifecycle_events_version"),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_material_lifecycle_events_request"),
        sa.CheckConstraint("version BETWEEN 1 AND 2147483647", name="ck_material_lifecycle_events_version"),
        sa.CheckConstraint("(action = 'ARCHIVE' AND version % 2 = 1) OR (action = 'RESTORE' AND version % 2 = 0)", name="ck_material_lifecycle_events_action"),
        sa.CheckConstraint("request_key != '00000000-0000-0000-0000-000000000000'", name="ck_material_lifecycle_events_key"),
        sa.CheckConstraint("length(trim(reason)) BETWEEN 1 AND 2000", name="ck_material_lifecycle_events_reason"),
        sa.CheckConstraint("review_generation > 0", name="ck_material_lifecycle_events_generation"),
        _hash("input_hash", "ck_material_lifecycle_events_input_hash"),
        _hash("request_hash", "ck_material_lifecycle_events_request_hash"))
    op.create_table("material_lifecycle_states",
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["material_id", "version"], ["material_lifecycle_events.material_id", "material_lifecycle_events.version"],
            name="fk_material_lifecycle_states_event", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"),
        sa.CheckConstraint("version BETWEEN 1 AND 2147483647", name="ck_material_lifecycle_states_version"),
        sa.CheckConstraint("(is_archived AND version % 2 = 1) OR (NOT is_archived AND version % 2 = 0)", name="ck_material_lifecycle_states_action"))
    op.execute("""CREATE FUNCTION material_lifecycle_state_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM id FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
        IF TG_OP='INSERT' THEN
            IF NEW.version != 1 OR NOT NEW.is_archived THEN RAISE EXCEPTION 'Lifecycle must start with archive'; END IF;
        ELSE
            IF NEW.material_id IS DISTINCT FROM OLD.material_id OR NEW.version::bigint != OLD.version::bigint+1
                OR NEW.is_archived = OLD.is_archived OR NEW.changed_at < OLD.changed_at THEN
                RAISE EXCEPTION 'Lifecycle must advance exactly once';
            END IF;
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("""CREATE FUNCTION material_lifecycle_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE current_state material_lifecycle_states%ROWTYPE; material pbr_materials%ROWTYPE;
        review material_review_states%ROWTYPE; last_version integer; last_generation bigint;
    BEGIN
        SELECT * INTO material FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'Lifecycle target is missing'; END IF;
        SELECT * INTO current_state FROM material_lifecycle_states WHERE material_id=NEW.material_id;
        IF NOT FOUND OR current_state.version != NEW.version OR current_state.changed_at IS DISTINCT FROM NEW.created_at
            OR current_state.is_archived IS DISTINCT FROM (NEW.action='ARCHIVE') THEN
            RAISE EXCEPTION 'Lifecycle history must match current state';
        END IF;
        SELECT coalesce(max(version),0), coalesce(max(review_generation),0) INTO last_version, last_generation
            FROM material_lifecycle_events WHERE material_id=NEW.material_id;
        IF NEW.version::bigint != last_version::bigint+1 THEN RAISE EXCEPTION 'Lifecycle history must advance in order'; END IF;
        SELECT * INTO review FROM material_review_states WHERE material_id=NEW.material_id;
        IF NOT FOUND OR review.generation != NEW.review_generation OR NEW.review_generation <= last_generation OR review.inventory_id IS NOT NULL
            OR review.revision_hash IS NOT NULL OR review.technical_check_id IS NOT NULL THEN
            RAISE EXCEPTION 'Lifecycle must invalidate current review';
        END IF;
        IF material.is_published OR material.publication_status != 'NOT_PUBLISHED'
            OR material.workflow_status != 'IN_PROGRESS' OR material.validation_status != 'NOT_CHECKED' THEN
            RAISE EXCEPTION 'Lifecycle requires unpublished unfinished material';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pbr_material_metadata WHERE material_id=NEW.material_id
            AND current_snapshot_id IS NULL AND status='NOT_SCANNED' AND source_content IS NULL) THEN
            RAISE EXCEPTION 'Lifecycle must reset current metadata';
        END IF;
        IF EXISTS (SELECT 1 FROM material_file_operations WHERE material_id=NEW.material_id AND status IN ('RUNNING','RECOVERY_REQUIRED'))
            OR EXISTS (SELECT 1 FROM material_packaging_states WHERE material_id=NEW.material_id AND status IN ('RESERVED','RUNNING','RETRY_REQUIRED','RECOVERY_REQUIRED'))
            OR EXISTS (SELECT 1 FROM publication_staging_owners WHERE material_id=NEW.material_id AND active) THEN
            RAISE EXCEPTION 'Lifecycle is blocked by active ownership';
        END IF;
        IF EXISTS (SELECT 1 FROM publication_staging_dispatches d
            JOIN publication_staging_items i ON i.job_id=d.job_id WHERE i.material_id=NEW.material_id) THEN
            RAISE EXCEPTION 'Lifecycle requires reconciled external state';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER material_lifecycle_state_guard BEFORE INSERT OR UPDATE ON material_lifecycle_states FOR EACH ROW EXECUTE FUNCTION material_lifecycle_state_guard()")
    op.execute("CREATE TRIGGER material_lifecycle_states_no_delete BEFORE DELETE OR TRUNCATE ON material_lifecycle_states FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    op.execute("CREATE TRIGGER material_lifecycle_event_guard BEFORE INSERT ON material_lifecycle_events FOR EACH ROW EXECUTE FUNCTION material_lifecycle_event_guard()")
    op.execute("CREATE TRIGGER material_lifecycle_events_no_update BEFORE UPDATE OR DELETE OR TRUNCATE ON material_lifecycle_events FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM material_lifecycle_states) OR EXISTS (SELECT 1 FROM material_lifecycle_events) THEN RAISE EXCEPTION 'Material lifecycle history exists; use a forward migration'; END IF; END $$")
    op.drop_table("material_lifecycle_states")
    op.drop_table("material_lifecycle_events")
    op.execute("DROP FUNCTION material_lifecycle_state_guard()")
    op.execute("DROP FUNCTION material_lifecycle_event_guard()")
