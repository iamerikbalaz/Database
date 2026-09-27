"""Global ZIP rules and phase-one publication without manufactured approvals."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260927_0031"
down_revision = "20260927_0030"
branch_labels = None
depends_on = None


def _policy_chain(automatic):
    verify_settings = """IF NEW.evidence->>'selection_mode' = 'AUTOMATIC' THEN
            IF NOT COALESCE((NEW.evidence->>'schema_version' = '2'
                AND NEW.evidence->'packaging_settings'->>'storage_timezone' = NEW.storage_timezone
                AND NEW.evidence->'packaging_settings'->>'before_method' = 'A'
                AND NEW.evidence->'packaging_settings'->>'on_or_after_method' = 'B'
                AND NEW.evidence->'packaging_settings'->>'legacy_zip_timestamp' = '2026-01-01T00:00:00'), false) THEN
                RAISE EXCEPTION 'Automatic policy requires complete global settings evidence';
            END IF;
            SELECT response_snapshot INTO config FROM packaging_settings_revisions ORDER BY version DESC LIMIT 1;
            IF (config IS NOT NULL AND NEW.evidence->'packaging_settings' IS DISTINCT FROM config)
                OR (config IS NULL AND NOT COALESCE((NEW.evidence->'packaging_settings'->>'version' = '0'
                    AND NEW.evidence->'packaging_settings'->>'cutoff_date' = '2026-03-04'), false)) THEN
                RAISE EXCEPTION 'Automatic policy must use the current global settings';
            END IF;
        END IF;""" if automatic else ""
    exception = """AND NOT COALESCE((NEW.evidence->>'selection_mode' = 'AUTOMATIC'
                AND NEW.evidence->>'schema_version' = '2'
                AND jsonb_typeof(NEW.evidence->'packaging_settings') = 'object'
                AND NEW.evidence->'packaging_settings'->>'storage_timezone' = NEW.storage_timezone), false)""" if automatic else ""
    op.execute(f"""CREATE OR REPLACE FUNCTION material_packaging_policy_chain() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE prior material_packaging_policies%ROWTYPE; config jsonb;
    BEGIN
        PERFORM 1 FROM pbr_materials WHERE id = NEW.material_id FOR UPDATE;
        SELECT * INTO prior FROM material_packaging_policies WHERE material_id = NEW.material_id ORDER BY revision DESC LIMIT 1;
        {verify_settings}
        IF (prior.id IS NULL AND (NEW.revision <> 1 OR NEW.previous_id IS NOT NULL))
            OR (prior.id IS NOT NULL AND (NEW.revision <> prior.revision + 1 OR NEW.previous_id IS DISTINCT FROM prior.id
                OR ((NEW.policy = prior.policy OR NEW.storage_timezone <> prior.storage_timezone) {exception}))) THEN
            RAISE EXCEPTION 'Packaging policy must extend the current decision';
        END IF;
        RETURN NEW;
    END; $$""")


def _packaging_guard(automatic):
    choose = """IF source.snapshot->>'schema_version' = '2' THEN
            SELECT * INTO policy FROM material_packaging_policies WHERE id=NEW.policy_id AND material_id=NEW.material_id;
            IF policy.evidence->'packaging_settings' IS DISTINCT FROM source.snapshot->'packaging_settings' THEN
                RAISE EXCEPTION 'Packaging policy must bind the saved global settings';
            END IF;
        ELSE
            SELECT * INTO policy FROM material_packaging_policies WHERE material_id=NEW.material_id ORDER BY revision DESC LIMIT 1;
        END IF;""" if automatic else "SELECT * INTO policy FROM material_packaging_policies WHERE material_id=NEW.material_id ORDER BY revision DESC LIMIT 1;"
    op.execute(f"""CREATE OR REPLACE FUNCTION packaging_inputs_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE material pbr_materials%ROWTYPE; policy material_packaging_policies%ROWTYPE; source publication_batch_items%ROWTYPE;
    BEGIN
        SELECT * INTO material FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
        SELECT * INTO source FROM publication_batch_items WHERE batch_id=NEW.batch_id AND material_id=NEW.material_id;
        {choose}
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
            RAISE EXCEPTION 'Packaging inputs must bind the batch and its policy';
        END IF;
        IF array_to_string(ARRAY(SELECT jsonb_array_elements_text(NEW.worker_request->'request'->'parts')), '/')
                IS DISTINCT FROM NEW.folder_path THEN
            RAISE EXCEPTION 'Packaging inputs must bind the material folder';
        END IF;
        RETURN NEW;
    END; $$""")


def upgrade():
    remainder = "request_hash"
    for char in "0123456789abcdef": remainder = f"replace({remainder}, '{char}', '')"
    op.create_table("packaging_settings_revisions",
        sa.Column("version", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("cutoff_date", sa.Date(), nullable=False),
        sa.Column("storage_timezone", sa.String(100), nullable=False),
        sa.Column("response_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_packaging_settings_actor_request"),
        sa.CheckConstraint("version >= 1", name="ck_packaging_settings_version"),
        sa.CheckConstraint("length(storage_timezone) BETWEEN 1 AND 100", name="ck_packaging_settings_timezone"),
        sa.CheckConstraint(f"length(request_hash) = 64 AND {remainder} = ''", name="ck_packaging_settings_request_hash"))
    op.execute("CREATE TRIGGER packaging_settings_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON packaging_settings_revisions "
        "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    op.execute("""CREATE FUNCTION packaging_settings_chain() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM pg_advisory_xact_lock(737824935);
        IF NEW.version <> COALESCE((SELECT max(version) FROM packaging_settings_revisions), 0) + 1
            OR NEW.response_snapshot->>'version' IS DISTINCT FROM NEW.version::text
            OR NEW.response_snapshot->>'cutoff_date' IS DISTINCT FROM NEW.cutoff_date::text
            OR NEW.response_snapshot->>'storage_timezone' IS DISTINCT FROM NEW.storage_timezone THEN
            RAISE EXCEPTION 'Settings must extend the current version';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER packaging_settings_chain BEFORE INSERT ON packaging_settings_revisions "
        "FOR EACH ROW EXECUTE FUNCTION packaging_settings_chain()")
    op.drop_constraint("fk_publication_batch_items_content", "publication_batch_items", type_="foreignkey")
    for field in ("technical_approval_id", "publication_approval_id"):
        op.alter_column("publication_batch_items", field, nullable=True)
    _policy_chain(True)
    _packaging_guard(True)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM packaging_settings_revisions)
            OR EXISTS (SELECT 1 FROM publication_batch_items WHERE snapshot->>'schema_version' = '2'
                OR technical_approval_id IS NULL OR publication_approval_id IS NULL)
            OR EXISTS (SELECT 1 FROM material_packaging_policies WHERE evidence->>'selection_mode' = 'AUTOMATIC') THEN
            RAISE EXCEPTION 'Global packaging or phase-one publication history exists; use a forward migration';
        END IF;
    END $$""")
    _packaging_guard(False)
    _policy_chain(False)
    for field in ("technical_approval_id", "publication_approval_id"):
        op.alter_column("publication_batch_items", field, nullable=False)
    op.create_foreign_key("fk_publication_batch_items_content", "publication_batch_items", "material_content_approvals",
        ["material_id", "content_context_hash"], ["material_id", "context_hash"], ondelete="RESTRICT")
    op.drop_table("packaging_settings_revisions")
    op.execute("DROP FUNCTION packaging_settings_chain()")
