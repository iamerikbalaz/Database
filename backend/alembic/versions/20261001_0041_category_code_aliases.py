"""Reserve category codes so historical material identities retain their category."""
from alembic import op
import sqlalchemy as sa

revision = "20261001_0041"
down_revision = "20261001_0040"
branch_labels = None
depends_on = None

_CANONICAL = """VALUES
('A','artificial'),
('A01','artificial / vinyl floors'),
('A02','artificial / laminate'),
('A03','artificial / plastics'),
('B','tiles'),
('B01','tiles / rectangular'),
('B03','tiles / patterns'),
('B02','tiles / terazzo'),
('B04','tiles / mosaics'),
('C','coatings'),
('D','concrete'),
('D01','concrete / bare'),
('D02','concrete / panels'),
('D03','concrete / pavement'),
('D04','concrete / rough'),
('E','constructions'),
('E01','constructions / corrugated sheets'),
('E02','constructions / acoustic panels'),
('E03','constructions / fences'),
('E04','constructions / roads-sidewalks'),
('E05','constructions / nets'),
('F','fabrics'),
('F01','fabrics / carpets'),
('F02','fabrics / curtains'),
('F03','fabrics / upholstery'),
('F04','fabrics / blinds'),
('F06','fabrics / boucle'),
('F07','fabrics / lace'),
('F08','fabrics / sheers'),
('F09','fabrics / satin'),
('F10','fabrics / velour'),
('F11','fabrics / velvet'),
('F12','fabrics / wool'),
('G','facade'),
('G02','facade / roof'),
('G03','facade / tiles'),
('G01','facade / bricks'),
('H','glass'),
('I','gravel'),
('J','leather'),
('K','metal'),
('K01','metal / perforated'),
('K02','metal / rust'),
('K03','metal / tiles'),
('K04','metal / generic'),
('L','miscellaneous'),
('L01','miscellaneous / decals'),
('M','natural'),
('M01','natural / grass'),
('M02','natural / ground'),
('M03','natural / snow'),
('N','plasters'),
('N01','plasters / facade'),
('N02','plasters / decorative'),
('N03','plasters / metallic'),
('N04','plasters / brushed'),
('N05','plasters / natural'),
('O','stones'),
('O01','stones / artificial'),
('O02','stones / natural'),
('O03','stones / pavement'),
('O04','stones / wall'),
('O05','stones / gabions'),
('O06','stones / onyx'),
('O07','stones / travertine'),
('O09','stones / marble'),
('P','wallpapers'),
('Q','wood'),
('Q01','wood / floor'),
('Q02','wood / planks'),
('Q03','wood / others'),
('Q04','wood / veneers'),
('Q05','wood / rattan'),
('Q06','wood / edges'),
('R','free')"""


def upgrade():
    op.create_table("online_category_codes",
        sa.Column("code", sa.String(100), primary_key=True),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("online_categories.id", ondelete="RESTRICT"), nullable=False))
    op.create_index("ix_online_category_codes_category_id", "online_category_codes", ["category_id"])
    # Historical audit receipts remain immutable. Ambiguous reuse of a historical
    # code intentionally aborts the upgrade rather than reassigning material data.
    op.execute("""INSERT INTO online_category_codes(code, category_id)
        SELECT DISTINCT code, category_id FROM (
          SELECT abbreviation AS code, id AS category_id FROM online_categories
          UNION ALL
          SELECT result->'audit'->'before'->>'abbreviation', resource_id FROM catalog_audit_events
            WHERE resource_kind='CATEGORY' AND result->'audit'->>'property'='identity'
          UNION ALL
          SELECT result->'audit'->'after'->>'abbreviation', resource_id FROM catalog_audit_events
            WHERE resource_kind='CATEGORY' AND result->'audit'->>'property'='identity'
          UNION ALL
          SELECT result->'audit'->>'before', resource_id FROM catalog_audit_events
            WHERE resource_kind='CATEGORY' AND result->'audit'->>'property'='abbreviation'
          UNION ALL
          SELECT result->'audit'->>'after', resource_id FROM catalog_audit_events
            WHERE resource_kind='CATEGORY' AND result->'audit'->>'property'='abbreviation'
        ) claims WHERE code IS NOT NULL AND code <> ''""")
    op.execute(f"""WITH canonical(code,path) AS ({_CANONICAL}), names AS (
        SELECT id AS category_id, normalized_key AS path FROM online_categories
        UNION
        SELECT resource_id, lower(regexp_replace(trim(result->'audit'->'before'->>'value'), '[[:space:]]+', ' ', 'g'))
          FROM catalog_audit_events WHERE resource_kind='CATEGORY' AND result->'audit'->>'property'='identity'
        ) INSERT INTO online_category_codes(code,category_id)
          SELECT DISTINCT c.code,n.category_id FROM canonical c JOIN names n ON n.path=c.path
          WHERE NOT EXISTS (SELECT 1 FROM online_category_codes saved WHERE saved.code=c.code)""")
    op.execute("""CREATE FUNCTION online_category_code_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Category codes are reserved and immutable'; END; $$""")
    op.execute("""CREATE TRIGGER online_category_codes_immutable BEFORE UPDATE OR DELETE ON online_category_codes
        FOR EACH ROW EXECUTE FUNCTION online_category_code_immutable()""")
    op.execute("""CREATE TRIGGER online_category_codes_no_truncate BEFORE TRUNCATE ON online_category_codes
        FOR EACH STATEMENT EXECUTE FUNCTION online_category_code_immutable()""")
    op.execute("""CREATE FUNCTION online_category_reserve_current_code() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM online_category_codes WHERE code=NEW.abbreviation AND category_id<>NEW.id) THEN
            RAISE EXCEPTION USING ERRCODE='23505', MESSAGE='Category abbreviation is reserved by another category';
          END IF;
          RETURN NEW;
        END; $$""")
    op.execute("""CREATE TRIGGER online_category_reserved_code BEFORE INSERT OR UPDATE OF abbreviation ON online_categories
        FOR EACH ROW EXECUTE FUNCTION online_category_reserve_current_code()""")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM online_category_codes code JOIN online_categories category ON category.id=code.category_id
                   WHERE code.code IS DISTINCT FROM category.abbreviation) THEN
          RAISE EXCEPTION 'Cannot downgrade while historical category code aliases exist';
        END IF;
      END; $$""")
    op.execute("DROP TRIGGER online_category_reserved_code ON online_categories")
    op.execute("DROP FUNCTION online_category_reserve_current_code()")
    op.drop_table("online_category_codes")
    op.execute("DROP FUNCTION online_category_code_immutable()")
