"""Required main-category projection; optional catalog membership stays separate.

Computed IDs identify a material property, never an invented persisted catalog row.
No reads mutate the catalog or existing immutable content history.
"""
from uuid import UUID, uuid5
from sqlalchemy import select
from app.db.models import OnlineCategory
from app.catalog import value_key

# Canonical paths shared with frontend/src/data/materialCategories.json.
CATEGORY_PATHS = {'A': 'Artificial', 'A01': 'Artificial / Vinyl floors', 'A02': 'Artificial / Laminate', 'A03': 'Artificial / Plastics', 'B': 'Tiles', 'B01': 'Tiles / Rectangular', 'B03': 'Tiles / Patterns', 'B02': 'Tiles / Terazzo', 'B04': 'Tiles / Mosaics', 'C': 'Coatings', 'D': 'Concrete', 'D01': 'Concrete / Bare', 'D02': 'Concrete / Panels', 'D03': 'Concrete / Pavement', 'D04': 'Concrete / Rough', 'E': 'Constructions', 'E01': 'Constructions / Corrugated sheets', 'E02': 'Constructions / Acoustic panels', 'E03': 'Constructions / Fences', 'E04': 'Constructions / Roads-Sidewalks', 'E05': 'Constructions / Nets', 'F': 'Fabrics', 'F01': 'Fabrics / Carpets', 'F02': 'Fabrics / Curtains', 'F03': 'Fabrics / Upholstery', 'F04': 'Fabrics / Blinds', 'F06': 'Fabrics / Boucle', 'F07': 'Fabrics / Lace', 'F08': 'Fabrics / Sheers', 'F09': 'Fabrics / Satin', 'F10': 'Fabrics / Velour', 'F11': 'Fabrics / Velvet', 'F12': 'Fabrics / Wool', 'G': 'Facade', 'G02': 'Facade / Roof', 'G03': 'Facade / Tiles', 'G01': 'Facade / Bricks', 'H': 'Glass', 'I': 'Gravel', 'J': 'Leather', 'K': 'Metal', 'K01': 'Metal / Perforated', 'K02': 'Metal / Rust', 'K03': 'Metal / Tiles', 'K04': 'Metal / Generic', 'L': 'Miscellaneous', 'L01': 'Miscellaneous / Decals', 'M': 'Natural', 'M01': 'Natural / Grass', 'M02': 'Natural / Ground', 'M03': 'Natural / Snow', 'N': 'Plasters', 'N01': 'Plasters / Facade', 'N02': 'Plasters / Decorative', 'N03': 'Plasters / Metallic', 'N04': 'Plasters / Brushed', 'N05': 'Plasters / Natural', 'O': 'Stones', 'O01': 'Stones / Artificial', 'O02': 'Stones / Natural', 'O03': 'Stones / Pavement', 'O04': 'Stones / Wall', 'O05': 'Stones / Gabions', 'O06': 'Stones / Onyx', 'O07': 'Stones / Travertine', 'O09': 'Stones / Marble', 'P': 'Wallpapers', 'Q': 'Wood', 'Q01': 'Wood / Floor', 'Q02': 'Wood / Planks', 'Q03': 'Wood / Others', 'Q04': 'Wood / Veneers', 'Q05': 'Wood / Rattan', 'Q06': 'Wood / Edges', 'R': 'Free'}
_NAMESPACE = UUID("ab6b96dc-52e5-4f72-a325-0f832dfaf180")


def required_category(session, material):
    code = material.main_category_code
    path = CATEGORY_PATHS.get(code, code)
    candidates = list(session.scalars(select(OnlineCategory).where(OnlineCategory.abbreviation == code)))
    catalog = candidates[0] if len(candidates) == 1 else session.scalar(select(OnlineCategory).where(OnlineCategory.normalized_key == value_key(path)))
    return {"id": str(catalog.id if catalog else uuid5(_NAMESPACE, code)),
        "value": catalog.value if catalog else path, "version": catalog.version if catalog else 1,
        "is_active": True, "is_required": True, "source": "MAIN_CATEGORY", "code": code,
        "catalog_id": str(catalog.id) if catalog else None}


def normalize_category_ids(session, material, category_ids):
    """Store only optional IDs; callers always project the required category."""
    required = required_category(session, material)
    return sorted({UUID(str(identifier)) for identifier in category_ids if str(identifier) != required["id"]})


def effective_category_ids(session, material, category_ids):
    return sorted({str(identifier) for identifier in normalize_category_ids(session, material, category_ids)} | {required_category(session, material)["id"]})


def effective_categories(session, material, categories):
    required = required_category(session, material)
    return [required, *[item for item in categories if item["id"] != required["id"]]]
