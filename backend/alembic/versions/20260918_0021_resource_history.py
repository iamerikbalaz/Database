"""Append-only ordinary brand, project, user and material changes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260918_0021"
down_revision = "20260918_0020"
branch_labels = None
depends_on = None


def _hash(column, name):
    remainder = column
    for character in "0123456789abcdef": remainder = f"replace({remainder}, '{character}', '')"
    return sa.CheckConstraint(f"{column} IS NULL OR (length({column}) = 64 AND {remainder} = '')", name=name)


def upgrade():
    op.create_table("resource_change_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("brand_id", sa.Uuid(), sa.ForeignKey("published_brands.id", ondelete="RESTRICT")),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="RESTRICT")),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT")),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT")),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("before_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("after_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("before_hash", sa.String(64), nullable=False),
        sa.Column("after_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        *[sa.UniqueConstraint(column, "version", name=f"uq_resource_change_events_{kind}_version")
            for kind, column in (("brand", "brand_id"), ("project", "project_id"), ("user", "user_id"), ("material", "material_id"))],
        sa.CheckConstraint("version BETWEEN 1 AND 2147483647", name="ck_resource_change_events_version"),
        sa.CheckConstraint("action IN ('CREATED', 'UPDATED')", name="ck_resource_change_events_action"),
        sa.CheckConstraint("(kind = 'BRAND' AND brand_id IS NOT NULL AND project_id IS NULL AND user_id IS NULL AND material_id IS NULL) OR "
            "(kind = 'PROJECT' AND project_id IS NOT NULL AND brand_id IS NULL AND user_id IS NULL AND material_id IS NULL) OR "
            "(kind = 'USER' AND user_id IS NOT NULL AND brand_id IS NULL AND project_id IS NULL AND material_id IS NULL) OR "
            "(kind = 'MATERIAL' AND material_id IS NOT NULL AND brand_id IS NULL AND project_id IS NULL AND user_id IS NULL)", name="ck_resource_change_events_target"),
        _hash("before_hash", "ck_resource_change_events_before_hash"),
        _hash("after_hash", "ck_resource_change_events_after_hash"))
    op.execute("""CREATE FUNCTION resource_change_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE actual jsonb; identifier uuid; keys text[]; last_version integer;
    BEGIN
        CASE NEW.kind
        WHEN 'BRAND' THEN
            identifier := NEW.brand_id;
            SELECT jsonb_build_object('id',id,'company_id',company_id,'name',name,'folder_prefix',folder_prefix,
                'brand_identifier',brand_identifier,'is_active',is_active) INTO actual
                FROM published_brands WHERE id=identifier FOR UPDATE;
        WHEN 'PROJECT' THEN
            identifier := NEW.project_id;
            SELECT jsonb_build_object('id',id,'company_id',company_id,'project_number',project_number,'name',name,
                'status',status,'due_date',due_date,'notes',notes) INTO actual
                FROM projects WHERE id=identifier FOR UPDATE;
        WHEN 'USER' THEN
            identifier := NEW.user_id;
            SELECT jsonb_build_object('id',id,'display_name',display_name,'email',email,'role',role,'is_active',is_active)
                INTO actual FROM internal_users WHERE id=identifier FOR UPDATE;
        WHEN 'MATERIAL' THEN
            identifier := NEW.material_id;
            SELECT jsonb_build_object('id',id,'project_id',project_id,'published_brand_id',published_brand_id,
                'sequence_number',sequence_number,'material_name',material_name,'main_category_code',main_category_code,
                'assigned_processor_id',assigned_processor_id,'technical_identity',technical_identity,'folder_path',folder_path,
                'workflow_status',workflow_status,'validation_status',validation_status,'is_published',is_published,
                'publication_status',publication_status) INTO actual FROM pbr_materials WHERE id=identifier FOR UPDATE;
        ELSE RAISE EXCEPTION 'Unsupported resource history kind';
        END CASE;
        IF actual IS NULL THEN RAISE EXCEPTION 'Resource history requires an existing target'; END IF;
        SELECT coalesce(max(version),0) INTO last_version FROM resource_change_events
            WHERE (NEW.kind='BRAND' AND brand_id=identifier) OR (NEW.kind='PROJECT' AND project_id=identifier)
                OR (NEW.kind='USER' AND user_id=identifier) OR (NEW.kind='MATERIAL' AND material_id=identifier);
        IF NEW.version::bigint != last_version::bigint + 1 THEN
            RAISE EXCEPTION 'Resource history must advance in order';
        END IF;
        IF NEW.after_snapshot IS DISTINCT FROM actual OR jsonb_typeof(NEW.before_snapshot) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'Resource history must contain exact record snapshots';
        END IF;
        keys := ARRAY(SELECT jsonb_object_keys(actual));
        IF NEW.action='CREATED' THEN
            IF NEW.version != 1 OR NEW.before_snapshot != '{}'::jsonb THEN
                RAISE EXCEPTION 'Resource creation must start history';
            END IF;
        ELSE
            IF NOT (NEW.before_snapshot ?& keys) OR NEW.before_snapshot - keys != '{}'::jsonb
                OR NEW.before_snapshot->>'id' IS DISTINCT FROM identifier::text OR NEW.before_snapshot = NEW.after_snapshot
                OR EXISTS (SELECT 1 FROM jsonb_each(NEW.before_snapshot) AS item WHERE jsonb_typeof(item.value) NOT IN ('string','number','boolean','null')) THEN
                RAISE EXCEPTION 'Resource changes require distinct whitelisted snapshots';
            END IF;
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER resource_change_guard BEFORE INSERT ON resource_change_events FOR EACH ROW EXECUTE FUNCTION resource_change_guard()")
    op.execute("CREATE TRIGGER resource_change_events_no_update BEFORE UPDATE OR DELETE OR TRUNCATE ON resource_change_events FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM resource_change_events) THEN RAISE EXCEPTION 'Resource history exists; use a forward migration'; END IF; END $$")
    op.drop_table("resource_change_events")
    op.execute("DROP FUNCTION resource_change_guard()")
