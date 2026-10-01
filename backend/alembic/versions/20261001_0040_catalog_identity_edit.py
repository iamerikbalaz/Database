"""Allow versioned catalog label corrections without renaming material data."""
from alembic import op

revision = "20261001_0040"
down_revision = "20261001_0039"
branch_labels = None
depends_on = None


def _guard(allow_names):
    mutable = "'is_active','version','updated_at','abbreviation'"
    changes = "NEW.is_active IS DISTINCT FROM OLD.is_active OR NEW.abbreviation IS DISTINCT FROM OLD.abbreviation"
    if allow_names:
        mutable += ",'value','normalized_key'"
        changes += " OR NEW.value IS DISTINCT FROM OLD.value OR NEW.normalized_key IS DISTINCT FROM OLD.normalized_key"
    op.execute(f"""CREATE OR REPLACE FUNCTION catalog_value_protect_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (to_jsonb(NEW) - ARRAY[{mutable}]) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY[{mutable}]) OR
             NEW.version != OLD.version + (CASE WHEN {changes} THEN 1 ELSE 0 END) THEN
            RAISE EXCEPTION 'Catalog identity is immutable and property changes must advance its version';
          END IF;
          RETURN NEW;
        END; $$""")


def upgrade():
    _guard(True)


def downgrade():
    # Restore the stricter rule without rewriting corrected labels or audit receipts.
    _guard(False)
