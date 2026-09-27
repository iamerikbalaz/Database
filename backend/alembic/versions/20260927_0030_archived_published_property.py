"""Preserve material properties and metadata when Archived changes.

Existing lifecycle evidence, active ownership fences and source state are retained.
"""
from alembic import op

revision = "20260927_0030"
down_revision = "20260926_0029"
branch_labels = None
depends_on = None


def _guard(*, published_archive):
    publication_guard = ("material.publication_status NOT IN ('NOT_PUBLISHED','PUBLISHED_CURRENT','PUBLISHED_UPDATE_REQUIRED')"
        if published_archive else "material.is_published OR material.publication_status != 'NOT_PUBLISHED'")
    workflow_guard = "" if published_archive else "OR material.workflow_status != 'IN_PROGRESS'"
    metadata_guard = "" if published_archive else """
        IF NOT EXISTS (SELECT 1 FROM pbr_material_metadata WHERE material_id=NEW.material_id
            AND current_snapshot_id IS NULL AND status='NOT_SCANNED' AND source_content IS NULL) THEN
            RAISE EXCEPTION 'Lifecycle must reset current metadata';
        END IF;"""
    op.execute(f"""CREATE OR REPLACE FUNCTION material_lifecycle_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE current_state material_lifecycle_states%ROWTYPE; material pbr_materials%ROWTYPE;
        review material_review_states%ROWTYPE; last_version integer; last_generation bigint;
    BEGIN
        SELECT * INTO material FROM pbr_materials WHERE id=NEW.material_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'Lifecycle target is missing'; END IF;
        SELECT * INTO current_state FROM material_lifecycle_states WHERE material_id=NEW.material_id;
        IF NOT FOUND OR current_state.version != NEW.version OR current_state.changed_at IS DISTINCT FROM NEW.created_at
            OR current_state.is_archived IS DISTINCT FROM (NEW.action='ARCHIVE') THEN
            RAISE EXCEPTION 'Lifecycle history must match current state';
        END IF;
        SELECT coalesce(max(version),0), coalesce(max(review_generation),0) INTO last_version, last_generation
            FROM material_lifecycle_events WHERE material_id=NEW.material_id;
        IF NEW.version::bigint != last_version::bigint+1 THEN RAISE EXCEPTION 'Lifecycle history must advance in order'; END IF;
        SELECT * INTO review FROM material_review_states WHERE material_id=NEW.material_id;
        IF NOT FOUND OR review.generation != NEW.review_generation OR NEW.review_generation <= last_generation OR review.inventory_id IS NOT NULL
            OR review.revision_hash IS NOT NULL OR review.technical_check_id IS NOT NULL THEN
            RAISE EXCEPTION 'Lifecycle must invalidate current review';
        END IF;
        IF {publication_guard}
            {workflow_guard} OR material.validation_status != 'NOT_CHECKED' THEN
            RAISE EXCEPTION 'Lifecycle requires inactive publication and invalidated review';
        END IF;
        {metadata_guard}
        IF EXISTS (SELECT 1 FROM material_file_operations WHERE material_id=NEW.material_id AND status IN ('RUNNING','RECOVERY_REQUIRED'))
            OR EXISTS (SELECT 1 FROM material_packaging_states WHERE material_id=NEW.material_id AND status IN ('RESERVED','RUNNING','RETRY_REQUIRED','RECOVERY_REQUIRED'))
            OR EXISTS (SELECT 1 FROM publication_staging_owners WHERE material_id=NEW.material_id AND active) THEN
            RAISE EXCEPTION 'Lifecycle is blocked by active ownership';
        END IF;
        IF EXISTS (SELECT 1 FROM publication_staging_dispatches d
            JOIN publication_staging_items i ON i.job_id=d.job_id WHERE i.material_id=NEW.material_id) THEN
            RAISE EXCEPTION 'Lifecycle requires reconciled external state';
        END IF;
        RETURN NEW;
    END; $$""")


def upgrade():
    _guard(published_archive=True)


def downgrade():
    # No evidence is removed; do not restore an incompatible rule over a current
    # material whose preserved properties require the new lifecycle contract.
    op.execute("""DO $$ BEGIN IF EXISTS (
        SELECT 1 FROM material_lifecycle_states s JOIN pbr_materials m ON m.id=s.material_id
        WHERE m.is_published OR m.publication_status != 'NOT_PUBLISHED' OR m.workflow_status != 'IN_PROGRESS'
            OR EXISTS (SELECT 1 FROM pbr_material_metadata metadata WHERE metadata.material_id=m.id
                AND (metadata.current_snapshot_id IS NOT NULL OR metadata.status != 'NOT_SCANNED' OR metadata.source_content IS NOT NULL))
    ) THEN RAISE EXCEPTION 'Preserved lifecycle properties exist; use a forward migration'; END IF; END; $$""")
    _guard(published_archive=False)
