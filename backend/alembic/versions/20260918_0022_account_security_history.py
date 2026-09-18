"""Append-only successful credential change metadata, without credentials."""
from alembic import op
import sqlalchemy as sa

revision = "20260918_0022"
down_revision = "20260918_0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("account_security_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT")),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("requires_password_change", sa.Boolean(), nullable=False),
        sa.Column("credential_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "version", name="uq_account_security_events_user_version"),
        sa.CheckConstraint("version BETWEEN 1 AND 2147483647", name="ck_account_security_events_version"),
        sa.CheckConstraint("(action IN ('FIRST_ADMIN_PROVISIONED','HOST_ACCESS_RECOVERED') AND actor_id IS NULL) OR "
            "(action IN ('ADMIN_ACCESS_PROVISIONED','ADMIN_ACCESS_RESET') AND actor_id IS NOT NULL AND actor_id != user_id) OR "
            "(action = 'SELF_PASSWORD_CHANGED' AND actor_id IS NOT NULL AND actor_id = user_id)", name="ck_account_security_events_actor"),
        sa.CheckConstraint("(action = 'SELF_PASSWORD_CHANGED' AND NOT requires_password_change) OR "
            "(action IN ('FIRST_ADMIN_PROVISIONED','HOST_ACCESS_RECOVERED','ADMIN_ACCESS_PROVISIONED','ADMIN_ACCESS_RESET') AND requires_password_change)",
            name="ck_account_security_events_outcome"))
    op.execute("""CREATE FUNCTION account_security_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE expected boolean; changed timestamptz; last_version integer;
    BEGIN
        -- Credential before profile, matching application/host lock order.
        SELECT must_change_password, password_changed_at INTO expected, changed
            FROM user_credentials WHERE user_id=NEW.user_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'Account security history requires a credential'; END IF;
        PERFORM id FROM internal_users WHERE id=NEW.user_id FOR UPDATE;
        SELECT coalesce(max(version),0) INTO last_version FROM account_security_events WHERE user_id=NEW.user_id;
        IF NEW.version::bigint != last_version::bigint + 1 THEN
            RAISE EXCEPTION 'Account security history must advance in order';
        END IF;
        IF NEW.requires_password_change IS DISTINCT FROM expected OR NEW.credential_changed_at IS DISTINCT FROM changed THEN
            RAISE EXCEPTION 'Account security history must match the credential outcome';
        END IF;
        IF NEW.action='FIRST_ADMIN_PROVISIONED' AND NEW.version != 1 THEN
            RAISE EXCEPTION 'Bootstrap must start account security history';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER account_security_event_guard BEFORE INSERT ON account_security_events FOR EACH ROW EXECUTE FUNCTION account_security_event_guard()")
    op.execute("CREATE TRIGGER account_security_events_no_update BEFORE UPDATE OR DELETE OR TRUNCATE ON account_security_events FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM account_security_events) THEN RAISE EXCEPTION 'Account security history exists; use a forward migration'; END IF; END $$")
    op.drop_table("account_security_events")
    op.execute("DROP FUNCTION account_security_event_guard()")
