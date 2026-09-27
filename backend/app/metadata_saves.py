"""Append a metadata snapshot and advance the current database projection."""
from datetime import UTC, datetime
from decimal import Decimal
from sqlalchemy import func, select

from app.db.models import PBRMaterialMetadata, PBRMaterialMetadataSnapshot
from app.metadata_client import MetadataValues


def persist_metadata_snapshot(session, material_id, values, *, status="WARNING", source_filename=None,
                              source_sha256=None, source_content=None, warnings=None, master_resolution=None):
    """Caller owns the material row lock, authorization, audit, and transaction.

    Historical database imports must leave source proof null and supply explicit
    provenance warnings. This helper never writes or claims to inspect a source.
    """
    values = MetadataValues.model_validate(values).model_dump()
    fields = {**values, "width_cm": Decimal(values["width_cm"]) if values["width_cm"] is not None else None,
              "height_cm": Decimal(values["height_cm"]) if values["height_cm"] is not None else None,
              "status": status, "source_filename": source_filename, "source_sha256": source_sha256,
              "source_content": source_content, "warnings": warnings or [], "loaded_at": datetime.now(UTC),
              "master_resolution": master_resolution}
    current = session.get(PBRMaterialMetadata, material_id)
    if current is None:
        current = PBRMaterialMetadata(material_id=material_id); session.add(current)
    sequence = session.scalar(select(func.max(PBRMaterialMetadataSnapshot.sequence_number)).where(
        PBRMaterialMetadataSnapshot.material_id == material_id)) or 0
    snapshot = PBRMaterialMetadataSnapshot(material_id=material_id, sequence_number=sequence + 1, **fields)
    session.add(snapshot); session.flush()
    current.current_snapshot_id = snapshot.id
    for name, value in fields.items(): setattr(current, name, value)
    return current
