"""Append-only company changes, without invented historical baseline events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260918_0020"
down_revision = "20260918_0019"
branch_labels = None
depends_on = None


def _hash(column, name):
    remainder = column
    for character in "0123456789abcdef": remainder = f"replace({remainder}, '{character}', '')"
    return sa.CheckConstraint(f"{column} IS NULL OR (length({column}) = 64 AND {remainder} = '')", name=name)


def upgrade():
    op.create_table("company_change_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(), sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("before_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("after_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("before_hash", sa.String(64), nullable=False),
        sa.Column("after_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", postgresql.JSONB(), nullable=False),
        sa.Column("request_key", sa.Uuid()), sa.Column("request_hash", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("company_id", "version", name="uq_company_change_events_version"),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_company_change_events_request"),
        sa.CheckConstraint("version BETWEEN 1 AND 2147483647", name="ck_company_change_events_version"),
        sa.CheckConstraint("action IN ('CREATED', 'UPDATED', 'NOTION_ADOPTED')", name="ck_company_change_events_action"),
        sa.CheckConstraint("(request_key IS NULL) = (request_hash IS NULL)", name="ck_company_change_events_request"),
        sa.CheckConstraint("length(reason) <= 2000 AND (action != 'NOTION_ADOPTED' OR (length(reason) >= 1 AND request_key IS NOT NULL))", name="ck_company_change_events_reason"),
        _hash("before_hash", "ck_company_change_events_before_hash"),
        _hash("after_hash", "ck_company_change_events_after_hash"),
        _hash("request_hash", "ck_company_change_events_request_hash"))
    op.create_index("ix_company_change_events_company_id", "company_change_events", ["company_id"])
    op.execute("""CREATE FUNCTION company_change_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE company companies%ROWTYPE; last_version integer; actual jsonb;
        keys text[] := ARRAY['id','name','legal_name','country','address','website','vat_id','notion_page_id','is_active'];
    BEGIN
        SELECT * INTO company FROM companies WHERE id=NEW.company_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'Company audit requires an existing company'; END IF;
        SELECT coalesce(max(version),0) INTO last_version FROM company_change_events WHERE company_id=NEW.company_id;
        IF NEW.version::bigint != last_version::bigint + 1 THEN
            RAISE EXCEPTION 'Company history must advance in order';
        END IF;
        actual := jsonb_build_object('id',company.id,'name',company.name,'legal_name',company.legal_name,
            'country',company.country,'address',company.address,'website',company.website,
            'vat_id',company.vat_id,'notion_page_id',company.notion_page_id,'is_active',company.is_active);
        IF NEW.after_snapshot IS DISTINCT FROM actual OR jsonb_typeof(NEW.before_snapshot) IS DISTINCT FROM 'object'
            OR jsonb_typeof(NEW.source) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'Company history must contain exact bounded record snapshots';
        END IF;
        IF NEW.action='CREATED' THEN
            IF NEW.version != 1 OR NEW.before_snapshot != '{}'::jsonb THEN
                RAISE EXCEPTION 'Company creation must start history';
            END IF;
        ELSE
            IF NOT (NEW.before_snapshot ?& keys) OR NEW.before_snapshot - keys != '{}'::jsonb
                OR NEW.before_snapshot->>'id' IS DISTINCT FROM NEW.company_id::text
                OR NEW.before_snapshot = NEW.after_snapshot THEN
                RAISE EXCEPTION 'Company changes require distinct explicit snapshots';
            END IF;
        END IF;
        IF NEW.action != 'NOTION_ADOPTED' AND NEW.source != '{}'::jsonb THEN
            RAISE EXCEPTION 'Local changes cannot claim remote provenance';
        END IF;
        IF NEW.action='NOTION_ADOPTED' THEN
            IF NOT (NEW.source ?& ARRAY['page_id','data_source_id','database_id','last_edited_time','mapping_sha256','observation_sha256','selected_fields'])
                OR NEW.source - ARRAY['page_id','data_source_id','database_id','last_edited_time','mapping_sha256','observation_sha256','selected_fields'] != '{}'::jsonb
                OR jsonb_typeof(NEW.source->'selected_fields') IS DISTINCT FROM 'array' THEN
                RAISE EXCEPTION 'Notion adoption requires explicit bounded provenance';
            END IF;
            IF EXISTS (SELECT 1 FROM unnest(ARRAY['page_id','data_source_id','database_id']) AS key(value)
                WHERE jsonb_typeof(NEW.source->value) IS DISTINCT FROM 'string'
                    OR NEW.source->>value !~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
                    OR NEW.source->>value = '00000000-0000-0000-0000-000000000000')
                OR replace(lower(NEW.before_snapshot->>'notion_page_id'),'-','') IS DISTINCT FROM replace(NEW.source->>'page_id','-','')
                OR EXISTS (SELECT 1 FROM unnest(ARRAY['mapping_sha256','observation_sha256']) AS key(value)
                    WHERE jsonb_typeof(NEW.source->value) IS DISTINCT FROM 'string' OR NEW.source->>value !~ '^[0-9a-f]{64}$')
                OR jsonb_typeof(NEW.source->'last_edited_time') IS DISTINCT FROM 'string'
                OR length(NEW.source->>'last_edited_time') NOT BETWEEN 1 AND 64
                OR NEW.source->>'last_edited_time' !~ 'T.*(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
                RAISE EXCEPTION 'Notion provenance must match the explicit company link';
            END IF;
            PERFORM (NEW.source->>'last_edited_time')::timestamptz;
            IF jsonb_array_length(NEW.source->'selected_fields') NOT BETWEEN 1 AND 6
                OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(NEW.source->'selected_fields') AS field(value)
                    WHERE value NOT IN ('name','legal_name','country','address','website','vat_id')
                        OR NEW.before_snapshot->value IS NOT DISTINCT FROM NEW.after_snapshot->value)
                OR (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(NEW.source->'selected_fields') AS field(value)) != jsonb_array_length(NEW.source->'selected_fields')
                OR (NEW.before_snapshot - ARRAY(SELECT jsonb_array_elements_text(NEW.source->'selected_fields')))
                    IS DISTINCT FROM (NEW.after_snapshot - ARRAY(SELECT jsonb_array_elements_text(NEW.source->'selected_fields'))) THEN
                RAISE EXCEPTION 'Notion adoption must change only selected descriptive fields';
            END IF;
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER company_change_guard BEFORE INSERT ON company_change_events FOR EACH ROW EXECUTE FUNCTION company_change_guard()")
    op.execute("CREATE TRIGGER company_change_events_no_update BEFORE UPDATE OR DELETE OR TRUNCATE ON company_change_events FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM company_change_events) THEN RAISE EXCEPTION 'Company history exists; use a forward migration'; END IF; END $$")
    op.drop_table("company_change_events")
    op.execute("DROP FUNCTION company_change_guard()")
