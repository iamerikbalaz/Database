"""Persist catalog abbreviations without rewriting publication snapshots."""
from alembic import op
import sqlalchemy as sa

from app.catalog import value_key
from app.texture_categories import CATEGORIES

revision = "20260926_0029"
down_revision = "20260926_0028"
branch_labels = None
depends_on = None


def _guard(*, abbreviations):
    mutable = "'is_active','version','updated_at','abbreviation'" if abbreviations else "'is_active','version','updated_at'"
    change = "NEW.is_active IS DISTINCT FROM OLD.is_active"
    if abbreviations:
        change += " OR NEW.abbreviation IS DISTINCT FROM OLD.abbreviation"
    op.execute(f"""CREATE OR REPLACE FUNCTION catalog_value_protect_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (to_jsonb(NEW) - ARRAY[{mutable}]) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY[{mutable}]) OR
             NEW.version != OLD.version + (CASE WHEN {change} THEN 1 ELSE 0 END) THEN
            RAISE EXCEPTION 'Catalog identity is immutable and property changes must advance its version';
          END IF;
          RETURN NEW;
        END; $$""")


def upgrade():
    for table in ("online_categories", "brand_collections"):
        op.add_column(table, sa.Column("abbreviation", sa.String(32), nullable=True))
    # Existing approval hashes include vocabulary versions. Backfill only the
    # administrative code, without advancing versions or altering identities.
    op.execute("DROP TRIGGER online_categories_protect_identity ON online_categories")
    connection = op.get_bind()
    for category in CATEGORIES:
        connection.execute(sa.text("UPDATE online_categories SET abbreviation=:code WHERE normalized_key=:key"),
                           {"code": category["code"], "key": value_key(category["value"])})
    _guard(abbreviations=True)
    op.execute("CREATE TRIGGER online_categories_protect_identity BEFORE UPDATE ON online_categories "
               "FOR EACH ROW EXECUTE FUNCTION catalog_value_protect_identity()")
    op.create_unique_constraint("uq_online_categories_abbreviation", "online_categories", ["abbreviation"])
    op.create_unique_constraint("uq_brand_collections_brand_abbreviation", "brand_collections", ["brand_id", "abbreviation"])
    for table in ("online_categories", "brand_collections"):
        op.create_check_constraint(f"ck_{table}_abbreviation", table,
                                   "abbreviation IS NULL OR abbreviation ~ '^[A-Z0-9][A-Z0-9_-]{0,31}$'")


def downgrade():
    connection = op.get_bind()
    # The bundled mapping can be reconstructed, but handwritten codes and
    # administrative edit receipts must never disappear on downgrade.
    known = {value_key(category["value"]): category["code"] for category in CATEGORIES}
    rows = connection.execute(sa.text("SELECT normalized_key, abbreviation FROM online_categories WHERE abbreviation IS NOT NULL"))
    if any(known.get(key) != code for key, code in rows) or connection.execute(sa.text(
            "SELECT EXISTS (SELECT 1 FROM brand_collections WHERE abbreviation IS NOT NULL) OR "
            "EXISTS (SELECT 1 FROM catalog_audit_events WHERE result->'body' ? 'abbreviation')")).scalar():
        raise RuntimeError("Catalog abbreviation data or edit history exists; use a forward migration.")
    _guard(abbreviations=False)
    op.drop_constraint("uq_online_categories_abbreviation", "online_categories", type_="unique")
    op.drop_constraint("uq_brand_collections_brand_abbreviation", "brand_collections", type_="unique")
    for table in ("online_categories", "brand_collections"):
        op.drop_constraint(f"ck_{table}_abbreviation", table, type_="check")
        op.drop_column(table, "abbreviation")
