"""Normalized publication categories, brand collections and versioned drafts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260916_0010"
down_revision = "20260916_0009"
branch_labels = None
depends_on = None


def _hash(column, name):
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return sa.CheckConstraint(f"length({column}) = 64 AND {remainder} = ''", name=name)


def _id():
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _material(*, primary=False):
    return sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False, primary_key=primary)


def _actor():
    return sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False)


def _created():
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade():
    for table in ("online_categories", "brand_collections"):
        columns = [_id(), sa.Column("value", sa.String(255), nullable=False),
            sa.Column("normalized_key", sa.String(765), nullable=False),
            sa.Column("version", sa.Integer(), server_default="1", nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False), _created(),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("version >= 1", name=f"ck_{table}_version")]
        if table == "brand_collections":
            columns += [sa.Column("brand_id", sa.Uuid(), sa.ForeignKey("published_brands.id", ondelete="RESTRICT"), nullable=False),
                        sa.UniqueConstraint("brand_id", "normalized_key", name="uq_brand_collections_brand_key")]
        else:
            columns += [sa.UniqueConstraint("normalized_key")]
        op.create_table(table, *columns)
    op.create_index("ix_brand_collections_brand_id", "brand_collections", ["brand_id"])
    op.execute("""CREATE FUNCTION catalog_value_protect_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (to_jsonb(NEW) - ARRAY['is_active','version','updated_at']) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY['is_active','version','updated_at']) OR
             NEW.version != OLD.version + (CASE WHEN NEW.is_active IS DISTINCT FROM OLD.is_active THEN 1 ELSE 0 END) THEN
            RAISE EXCEPTION 'Catalog identity is immutable and activity changes must advance its version';
          END IF;
          RETURN NEW;
        END; $$""")
    for table in ("online_categories", "brand_collections"):
        op.execute(f"CREATE TRIGGER {table}_protect_identity BEFORE UPDATE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION catalog_value_protect_identity()")
        op.execute(f"CREATE TRIGGER {table}_no_delete BEFORE DELETE OR TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    op.create_table("catalog_audit_events", _id(), _actor(),
        sa.Column("resource_id", sa.Uuid(), nullable=False), sa.Column("resource_kind", sa.String(20), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False), sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False), _created(),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_catalog_audit_events_actor_request"),
        _hash("request_hash", "ck_catalog_audit_events_request_hash"),
        sa.CheckConstraint("resource_kind IN ('CATEGORY', 'COLLECTION')", name="ck_catalog_audit_events_kind"))
    op.create_index("ix_catalog_audit_events_resource_id", "catalog_audit_events", ["resource_id"])
    op.create_table("material_content", _material(primary=True),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("description", sa.Text()),
        sa.Column("credits", sa.Integer()), sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_material_content_revision"),
        sa.CheckConstraint("credits IS NULL OR credits BETWEEN 0 AND 2147483647", name="ck_material_content_credits"))
    op.create_table("material_online_categories", _material(primary=True),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("online_categories.id", ondelete="RESTRICT"), primary_key=True))
    op.create_table("material_collections", _material(primary=True),
        sa.Column("collection_id", sa.Uuid(), sa.ForeignKey("brand_collections.id", ondelete="RESTRICT"), primary_key=True))
    op.create_table("material_content_revisions", _id(), _material(), _actor(),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False), sa.Column("reason", sa.Text(), nullable=False), _created(),
        sa.UniqueConstraint("material_id", "revision", name="uq_material_content_revisions_revision"),
        sa.CheckConstraint("revision >= 1", name="ck_material_content_revisions_revision"),
        _hash("snapshot_hash", "ck_material_content_revisions_snapshot_hash"))
    op.create_index("ix_material_content_revisions_material_id", "material_content_revisions", ["material_id"])
    for table in ("catalog_audit_events", "material_content_revisions"):
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    for table in ("material_content_revisions", "material_collections", "material_online_categories",
                  "material_content", "catalog_audit_events", "brand_collections", "online_categories"):
        op.drop_table(table)
    op.execute("DROP FUNCTION catalog_value_protect_identity()")
