"""Short-lived one-material AI credentials and permanent proposal attribution."""
from alembic import op
import sqlalchemy as sa

revision = "20260917_0014"
down_revision = "20260917_0013"
branch_labels = None
depends_on = None


def upgrade():
    remainder = "token_hash"
    for character in "0123456789abcdef": remainder = f"replace({remainder}, '{character}', '')"
    op.create_table("ai_service_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("issuer_session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(f"token_hash IS NULL OR (length(token_hash) = 64 AND {remainder} = '')", name="ck_ai_service_credentials_token_hash"),
        sa.CheckConstraint("expires_at > created_at", name="ck_ai_service_credentials_expiration"),
        sa.CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name="ck_ai_service_credentials_revocation"))
    op.create_index("ix_ai_service_credentials_material_id", "ai_service_credentials", ["material_id"])
    op.execute("""CREATE FUNCTION ai_service_credential_protect() RETURNS trigger AS $$ BEGIN
        IF ROW(NEW.id, NEW.material_id, NEW.actor_id, NEW.issuer_session_id, NEW.token_hash, NEW.created_at, NEW.expires_at)
           IS DISTINCT FROM ROW(OLD.id, OLD.material_id, OLD.actor_id, OLD.issuer_session_id, OLD.token_hash, OLD.created_at, OLD.expires_at)
           OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN
           RAISE EXCEPTION 'AI credential scope and revocation are immutable'; END IF;
        RETURN NEW; END; $$ LANGUAGE plpgsql""")
    op.execute("CREATE TRIGGER ai_service_credentials_protect BEFORE UPDATE ON ai_service_credentials FOR EACH ROW EXECUTE FUNCTION ai_service_credential_protect()")
    op.execute("CREATE TRIGGER ai_service_credentials_no_delete BEFORE DELETE OR TRUNCATE ON ai_service_credentials FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    op.add_column("material_ai_drafts", sa.Column("service_credential_id", sa.Uuid()))
    op.create_foreign_key("fk_material_ai_drafts_service_credential", "material_ai_drafts", "ai_service_credentials", ["service_credential_id"], ["id"], ondelete="RESTRICT")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM ai_service_credentials) THEN "
               "RAISE EXCEPTION 'AI credential provenance exists; preserve schema and use a forward migration'; END IF; END $$")
    op.drop_constraint("fk_material_ai_drafts_service_credential", "material_ai_drafts", type_="foreignkey")
    op.drop_column("material_ai_drafts", "service_credential_id")
    op.drop_index("ix_ai_service_credentials_material_id", table_name="ai_service_credentials")
    op.drop_table("ai_service_credentials")
    op.execute("DROP FUNCTION ai_service_credential_protect()")
