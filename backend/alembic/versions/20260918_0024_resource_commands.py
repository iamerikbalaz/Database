"""Actor-bound successful ordinary write receipts, preserving earlier history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260918_0024"
down_revision = "20260918_0023"
branch_labels = None
depends_on = None


def upgrade():
    targets = {"company_id": "companies", "brand_id": "published_brands", "project_id": "projects", "user_id": "internal_users", "material_id": "pbr_materials"}
    op.create_table("resource_commands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("action", sa.String(16), nullable=False),
        sa.Column("privilege", sa.String(16), nullable=False),
        *[sa.Column(field, sa.Uuid(), sa.ForeignKey(table + ".id", ondelete="RESTRICT")) for field, table in targets.items()],
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("response_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_resource_commands_actor_key"),
        sa.CheckConstraint("request_key != '00000000-0000-0000-0000-000000000000'", name="ck_resource_commands_key"),
        sa.CheckConstraint("action IN ('CREATED','UPDATED')", name="ck_resource_commands_action"),
        sa.CheckConstraint("(CASE WHEN company_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN brand_id IS NOT NULL THEN 1 ELSE 0 END + "
            "CASE WHEN project_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN user_id IS NOT NULL THEN 1 ELSE 0 END + "
            "CASE WHEN material_id IS NOT NULL THEN 1 ELSE 0 END) = 1 AND "
            "((kind='COMPANY' AND company_id IS NOT NULL) OR (kind='BRAND' AND brand_id IS NOT NULL) OR "
            "(kind='PROJECT' AND project_id IS NOT NULL) OR (kind='USER' AND user_id IS NOT NULL) OR "
            "(kind='MATERIAL' AND material_id IS NOT NULL))", name="ck_resource_commands_target"),
        sa.CheckConstraint("(kind='USER' AND privilege='ADMIN') OR (kind IN ('COMPANY','BRAND','PROJECT') AND privilege='CATALOG') OR "
            "(kind='MATERIAL' AND (privilege='CATALOG' OR (action='UPDATED' AND privilege='MATERIAL_NAME')))", name="ck_resource_commands_privilege"),
        *[sa.CheckConstraint(f"{field} ~ '^[a-f0-9]{{64}}$'", name=f"ck_resource_commands_{field}") for field in ("request_hash", "response_hash")])
    op.execute("""CREATE FUNCTION resource_command_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE actual jsonb; expected_keys text[]; target_id uuid;
    BEGIN
        CASE NEW.kind
        WHEN 'COMPANY' THEN
            SELECT to_jsonb(c) INTO actual FROM companies c WHERE id=NEW.company_id;
            expected_keys := ARRAY['id','name','legal_name','country','address','website','vat_id','notion_page_id','is_active','created_at','updated_at'];
            target_id := NEW.company_id;
        WHEN 'BRAND' THEN
            SELECT to_jsonb(b) INTO actual FROM published_brands b WHERE id=NEW.brand_id;
            expected_keys := ARRAY['id','company_id','name','folder_prefix','brand_identifier','is_active','next_sequence_number','created_at','updated_at'];
            target_id := NEW.brand_id;
        WHEN 'PROJECT' THEN
            SELECT to_jsonb(p) INTO actual FROM projects p WHERE id=NEW.project_id;
            expected_keys := ARRAY['id','company_id','project_number','name','status','due_date','notes','created_at','updated_at'];
            target_id := NEW.project_id;
        WHEN 'USER' THEN
            SELECT to_jsonb(u) INTO actual FROM internal_users u WHERE id=NEW.user_id;
            expected_keys := ARRAY['id','display_name','email','role','is_active','created_at','updated_at'];
            target_id := NEW.user_id;
        WHEN 'MATERIAL' THEN
            SELECT to_jsonb(m) INTO actual FROM pbr_materials m WHERE id=NEW.material_id;
            expected_keys := ARRAY['id','project_id','published_brand_id','sequence_number','material_name','main_category_code','assigned_processor_id',
                'technical_identity','folder_path','workflow_status','validation_status','is_published','publication_status','created_at','updated_at'];
            target_id := NEW.material_id;
        ELSE RAISE EXCEPTION 'Unsupported resource command';
        END CASE;
        IF actual IS NULL OR jsonb_typeof(NEW.response_snapshot) IS DISTINCT FROM 'object'
            OR octet_length(NEW.response_snapshot::text) > 65536 THEN
            RAISE EXCEPTION 'Invalid resource command response';
        END IF;
        IF NOT NEW.response_snapshot ?& expected_keys OR NEW.response_snapshot - expected_keys != '{}'::jsonb
            OR NEW.response_snapshot->>'id' IS DISTINCT FROM target_id::text
            OR (NEW.response_snapshot->>'created_at')::timestamptz IS DISTINCT FROM (actual->>'created_at')::timestamptz
            OR (NEW.response_snapshot->>'updated_at')::timestamptz IS DISTINCT FROM (actual->>'updated_at')::timestamptz
            OR NEW.response_snapshot - ARRAY['created_at','updated_at'] IS DISTINCT FROM actual - ARRAY['created_at','updated_at'] THEN
            RAISE EXCEPTION 'Resource command response must match the exact record';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER resource_command_guard BEFORE INSERT ON resource_commands FOR EACH ROW EXECUTE FUNCTION resource_command_guard()")
    op.execute("CREATE TRIGGER resource_commands_no_update BEFORE UPDATE OR DELETE OR TRUNCATE ON resource_commands FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM resource_commands) THEN RAISE EXCEPTION 'Resource command receipts exist; use a forward migration'; END IF; END $$")
    op.drop_table("resource_commands")
    op.execute("DROP FUNCTION resource_command_guard()")
