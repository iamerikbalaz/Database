"""Complete real identity facts atomically; never perform filesystem operations."""
from fastapi import HTTPException
from sqlalchemy import select
from app.db.models import PublishedBrand, MaterialNumberReservation, MaterialIdentityHistory
from app.material_identity import identity_context, require_brand_idle
from app.material_naming import build_identity
from app.main_category import require_current_category_code


def finish_draft_identity(session, material, actor_id):
    if not material.is_draft or material.published_brand_id is None or material.main_category_code is None:
        return False
    brand = session.scalar(select(PublishedBrand).where(PublishedBrand.id == material.published_brand_id).with_for_update())
    if brand is None or not brand.is_active or not brand.is_customer:
        raise HTTPException(409, {"code": "MATERIAL_CUSTOMER_INACTIVE"})
    require_brand_idle(session, brand.id)
    require_current_category_code(session, material.main_category_code)
    if brand.next_sequence_number > 9999:
        raise HTTPException(409, {"code": "MATERIAL_SEQUENCE_EXHAUSTED"})
    before = identity_context(material)
    number = brand.next_sequence_number
    material.sequence_number = number
    material.source_brand_name = brand.name
    try:
        material.technical_identity = build_identity(brand.folder_prefix, number, material.main_category_code, material.material_name)
    except ValueError:
        raise HTTPException(422, {"code": "MATERIAL_IDENTITY_INVALID"}) from None
    material.is_draft = False
    brand.next_sequence_number += 1
    session.add(MaterialNumberReservation(brand_id=brand.id, sequence_number=number, material_id=material.id, actor_id=actor_id))
    session.add(MaterialIdentityHistory(material_id=material.id, actor_id=actor_id, old_context=before,
        new_context=identity_context(material), reason="Draft identity completed before creating a material folder."))
    return True
