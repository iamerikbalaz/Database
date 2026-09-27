"""Maintain a company's same-name brand without renaming existing sources."""
import re
import unicodedata

from sqlalchemy import select

from app.db.models import PublishedBrand
from app.resource_history import append_resource_change


def ensure_company_brand(session, company, actor_id):
    """Caller holds the exclusive access gate. Returns the existing/created brand."""
    session.flush()
    existing = list(session.scalars(select(PublishedBrand).where(PublishedBrand.company_id == company.id)))
    name = unicodedata.normalize("NFC", company.name).strip().casefold()
    matching = next((brand for brand in existing if unicodedata.normalize("NFC", brand.name).strip().casefold() == name), None)
    if matching is not None: return matching
    stem = unicodedata.normalize("NFKD", company.name).encode("ascii", "ignore").decode().upper()
    stem = re.sub(r"[^A-Z0-9]+", "-", stem).strip("-")[:220] or "COMPANY"
    # These are provisional database identifiers, not a guess at a NAS prefix
    # or the brand identifier supplied by the historical workbook.
    prefix, identifier = stem + "-COMPANY", stem.lower() + "-company"
    used_prefixes = set(session.scalars(select(PublishedBrand.folder_prefix)))
    used_identifiers = set(session.scalars(select(PublishedBrand.brand_identifier)))
    if prefix in used_prefixes: prefix += "-" + company.id.hex[:12].upper()
    if identifier in used_identifiers: identifier += "-" + company.id.hex[:12]
    # A pre-existing imported label can occupy the deterministic suffix too.
    count = 2
    while prefix in used_prefixes or identifier in used_identifiers:
        prefix, identifier = f"{stem}-{company.id.hex[:12].upper()}-{count}", f"{stem.lower()}-{company.id.hex[:12]}-{count}"
        count += 1
    brand = PublishedBrand(company_id=company.id, name=company.name, folder_prefix=prefix,
        brand_identifier=identifier, next_sequence_number=1, is_active=company.is_active)
    session.add(brand)
    append_resource_change(session, brand, actor_id, {}, action="CREATED")
    return brand
