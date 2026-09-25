"""Folder identities. Imported names are preserved; only new names are normalized."""
import re
import unicodedata

# Keep the reader grammar aligned with worker.app.material_naming. Try the named
# form first: a numeric product name must not be mistaken for a legacy prefix.
NAMED_IDENTITY = re.compile(r"(?P<prefix>[A-Za-z0-9_-]+)_(?P<number>[0-9]{4})_(?P<name>[A-Za-z0-9.-]+)_(?P<category>[A-Z0-9-]+)")
LEGACY_IDENTITY = re.compile(r"(?P<prefix>[A-Za-z0-9_-]+)_(?P<number>[0-9]{4})_(?P<category>[A-Z0-9-]+)")


def match_identity(value: str):
    """Read both persisted formats without rewriting any existing identity."""
    if len(value) > 255:
        return None
    match = NAMED_IDENTITY.fullmatch(value) or LEGACY_IDENTITY.fullmatch(value)
    if match is None or int(match["number"]) == 0:
        return None
    if "name" in match.re.groupindex and not any(char.isalnum() for char in match["name"]):
        return None
    return match


def name_component(material_name: str) -> str:
    """Create a portable uppercase token; keep the display name unchanged."""
    decomposed = unicodedata.normalize("NFKD", material_name)
    latin = "".join(char for char in decomposed if not unicodedata.combining(char)).upper()
    token = re.sub(r"[^A-Z0-9.]+", "-", latin).strip("-.")
    if not token or not any(char.isalnum() for char in token):
        raise ValueError("MATERIAL_IDENTITY_INVALID")
    return token


def base_name(identity: str) -> str:
    match = match_identity(identity)
    return identity.rsplit("_", 1)[0] if match is not None and "name" in match.re.groupindex else identity


def map_bases(identity: str) -> tuple[str, ...]:
    # Read older full-folder-prefixed maps too, but never accept another asset's
    # prefix. Duplicate map shortcuts remain an error across both spellings.
    return tuple(dict.fromkeys((base_name(identity), identity)))


def build_identity(prefix: str, number: int, category: str, material_name: str, *, source_identity: str | None = None) -> str:
    if not 1 <= number <= 9999:
        raise ValueError("MATERIAL_IDENTITY_INVALID")
    # Category/rebrand operations retain an already recorded name component,
    # even when somebody has subsequently edited the display name.
    source = match_identity(source_identity) if source_identity is not None else None
    if source_identity is not None and source is None:
        raise ValueError("MATERIAL_IDENTITY_INVALID")
    token = source["name"] if source is not None and "name" in source.re.groupindex else name_component(material_name)
    value = f"{prefix}_{number:04d}_{token}_{category}"
    if match_identity(value) is None or NAMED_IDENTITY.fullmatch(value) is None:
        raise ValueError("MATERIAL_IDENTITY_INVALID")
    return value
