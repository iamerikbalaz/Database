"""Customer profiles and Orders retain existing brand/project identities."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260930_0034"
down_revision = "20260928_0033"
branch_labels = None
depends_on = None

CUSTOMER_COLUMNS = (
    "is_customer", "customer_status", "customer_brand_identifier", "website", "address", "shipping_address",
    "legal_name", "vat_id", "description", "notes", "notion_page_id", "logo_content", "logo_content_type", "logo_filename", "logo_sha256",
)
ORDER_COLUMNS = ("customer_id", "project_type", "starting_date", "responsible_id", "responsible_notion_page_ids", "order_status", "priority", "notion_page_id")


def _legacy_receipt_guard(upgrade):
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    definition = connection.execute(sa.text("SELECT pg_get_functiondef('resource_command_guard()'::regprocedure)")).scalar_one()
    for alias, columns in (("b", CUSTOMER_COLUMNS), ("p", ORDER_COLUMNS), ("u", ("notion_people_page_id",))):
        old = f"SELECT to_jsonb({alias}) INTO actual"
        new = f"SELECT to_jsonb({alias}) - ARRAY[" + ",".join(f"'{key}'" for key in columns) + "] INTO actual"
        source, replacement = (old, new) if upgrade else (new, old)
        if definition.count(source) != 1:
            raise RuntimeError("Unexpected legacy receipt guard; no changes applied")
        definition = definition.replace(source, replacement, 1)
    connection.execute(sa.text(definition))


def upgrade():
    op.add_column("internal_users", sa.Column("notion_people_page_id", sa.String(36)))
    op.create_unique_constraint("uq_internal_users_notion_people_page_id", "internal_users", ["notion_people_page_id"])
    columns = [
        sa.Column("is_customer", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("customer_status", sa.String(32), nullable=False, server_default="Active"),
        sa.Column("customer_brand_identifier", sa.String(255)),
        sa.Column("website", sa.String(2048)), sa.Column("address", sa.Text()),
        sa.Column("shipping_address", sa.Text()), sa.Column("legal_name", sa.String(255)),
        sa.Column("vat_id", sa.String(100)), sa.Column("description", sa.Text()), sa.Column("notes", sa.Text()),
        sa.Column("notion_page_id", sa.String(36)), sa.Column("logo_content", sa.LargeBinary()),
        sa.Column("logo_content_type", sa.String(50)), sa.Column("logo_filename", sa.String(255)),
        sa.Column("logo_sha256", sa.String(64)),
    ]
    for column in columns:
        op.add_column("published_brands", column)
    op.create_unique_constraint("uq_published_brands_notion_page_id", "published_brands", ["notion_page_id"])
    op.create_unique_constraint("uq_published_brands_customer_brand_identifier", "published_brands", ["customer_brand_identifier"])
    op.create_check_constraint("ck_customer_status", "published_brands", "customer_status IN ('In library','test','Active')")
    op.execute("""UPDATE published_brands b SET customer_brand_identifier=b.brand_identifier,
        website=c.website,address=c.address,legal_name=c.legal_name,vat_id=c.vat_id
        FROM companies c WHERE c.id=b.company_id""")
    # A Company page may describe several brands. Link only an unambiguous
    # single child; the one-time reviewed import resolves remaining matches.
    op.execute("""UPDATE published_brands b SET notion_page_id=c.notion_page_id FROM companies c
        WHERE c.id=b.company_id AND c.notion_page_id IS NOT NULL AND length(c.notion_page_id) IN (32,36)
        AND (SELECT count(*) FROM published_brands other WHERE other.company_id=c.id)=1""")
    for column in [
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("published_brands.id", ondelete="RESTRICT")),
        sa.Column("project_type", sa.String(255)), sa.Column("starting_date", sa.Date()),
        sa.Column("responsible_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT")),
        sa.Column("responsible_notion_page_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("order_status", sa.String(32), nullable=False, server_default="Not started"),
        sa.Column("priority", sa.String(16)), sa.Column("notion_page_id", sa.String(36)),
    ]:
        op.add_column("projects", column)
    op.create_index("ix_projects_customer_id", "projects", ["customer_id"])
    op.create_index("ix_projects_responsible_id", "projects", ["responsible_id"])
    op.create_unique_constraint("uq_projects_notion_page_id", "projects", ["notion_page_id"])
    op.create_check_constraint("ck_order_priority", "projects", "priority IS NULL OR priority IN ('Low','Medium','High','Urgent')")
    op.create_check_constraint("ck_order_status", "projects", "order_status IN ('Test complete','To be invoiced','Ongoing','Canceled','Done','Samples Obtained','Visualize','Waiting for samples','Post-production','Scanned','Price offer sent','invoiced','Not started')")
    op.execute("""UPDATE projects SET order_status=CASE status WHEN 'DONE' THEN 'Done' WHEN 'IN_PROGRESS' THEN 'Ongoing' ELSE 'Not started' END""")
    op.execute("""UPDATE projects p SET customer_id=b.id FROM published_brands b WHERE b.company_id=p.company_id
        AND (SELECT count(*) FROM published_brands other WHERE other.company_id=p.company_id)=1""")
    for table in ("directory_commands", "directory_change_events"):
        cols = [
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False), sa.Column("action", sa.String(20), nullable=False),
            sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("published_brands.id", ondelete="RESTRICT")),
            sa.Column("order_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="RESTRICT")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("(kind='CUSTOMER' AND customer_id IS NOT NULL AND order_id IS NULL) OR (kind='ORDER' AND order_id IS NOT NULL AND customer_id IS NULL)", name="ck_" + table + "_target"),
        ]
        if table == "directory_commands":
            cols += [sa.Column("request_key", sa.Uuid(), nullable=False), sa.Column("request_hash", sa.String(64), nullable=False),
                sa.Column("response_snapshot", postgresql.JSONB(), nullable=False), sa.Column("response_hash", sa.String(64), nullable=False),
                sa.UniqueConstraint("actor_id", "request_key", name="uq_directory_commands_actor_key"),
                sa.CheckConstraint("action IN ('CREATED','UPDATED','LOGO_UPDATED')", name="ck_directory_commands_action")]
        else:
            cols += [sa.Column("version", sa.Integer(), nullable=False),
                sa.Column("before_snapshot", postgresql.JSONB(), nullable=False), sa.Column("after_snapshot", postgresql.JSONB(), nullable=False),
                sa.Column("before_hash", sa.String(64), nullable=False), sa.Column("after_hash", sa.String(64), nullable=False),
                sa.UniqueConstraint("customer_id", "version", name="uq_directory_change_customer_version"),
                sa.UniqueConstraint("order_id", "version", name="uq_directory_change_order_version"),
                sa.CheckConstraint("version > 0", name="ck_directory_change_version")]
        op.create_table(table, *cols)
        op.execute(f"CREATE TRIGGER {table}_no_update BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    _legacy_receipt_guard(True)


def downgrade():
    connection = op.get_bind()
    # A schema-only trial can be rolled back. Once a new field, imported page,
    # receipt or audit has meaning, rollback must not erase it.
    changed = connection.execute(sa.text("""SELECT
        EXISTS(SELECT 1 FROM directory_commands) OR EXISTS(SELECT 1 FROM directory_change_events)
        OR EXISTS(SELECT 1 FROM internal_users WHERE notion_people_page_id IS NOT NULL)
        OR EXISTS(SELECT 1 FROM projects p WHERE project_type IS NOT NULL OR starting_date IS NOT NULL
            OR responsible_id IS NOT NULL OR responsible_notion_page_ids != '[]'::jsonb
            OR priority IS NOT NULL OR notion_page_id IS NOT NULL
            OR order_status IS DISTINCT FROM CASE status WHEN 'DONE' THEN 'Done' WHEN 'IN_PROGRESS' THEN 'Ongoing' ELSE 'Not started' END
            OR (customer_id IS NOT NULL AND customer_id IS DISTINCT FROM (SELECT min(b.id::text)::uuid FROM published_brands b
                WHERE b.company_id=p.company_id HAVING count(*)=1)))
        OR EXISTS(SELECT 1 FROM published_brands b JOIN companies c ON c.id=b.company_id
            WHERE NOT b.is_customer OR b.customer_status!='Active'
            OR b.customer_brand_identifier IS DISTINCT FROM b.brand_identifier
            OR b.website IS DISTINCT FROM c.website OR b.address IS DISTINCT FROM c.address
            OR b.legal_name IS DISTINCT FROM c.legal_name OR b.vat_id IS DISTINCT FROM c.vat_id
            OR b.shipping_address IS NOT NULL OR b.description IS NOT NULL OR b.notes IS NOT NULL
            OR b.logo_content IS NOT NULL OR b.logo_content_type IS NOT NULL OR b.logo_filename IS NOT NULL OR b.logo_sha256 IS NOT NULL
            OR b.notion_page_id IS DISTINCT FROM CASE WHEN length(c.notion_page_id) IN (32,36)
                AND (SELECT count(*) FROM published_brands sibling WHERE sibling.company_id=c.id)=1 THEN c.notion_page_id ELSE NULL END)
        """)).scalar_one()
    if changed:
        raise RuntimeError("Customers / Orders contain business data; use a forward migration")
    _legacy_receipt_guard(False)
    op.drop_table("directory_commands")
    op.drop_table("directory_change_events")
    for column in reversed(ORDER_COLUMNS):
        op.drop_column("projects", column)
    for column in reversed(CUSTOMER_COLUMNS):
        op.drop_column("published_brands", column)
    op.drop_column("internal_users", "notion_people_page_id")
