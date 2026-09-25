"""Identity grammar shared by source rename planning and package naming."""
import re

# Keep this reader aligned with backend.app.material_naming. Both persisted
# formats remain readable; creation in the backend always includes the name.
NAMED_IDENTITY = re.compile(r"(?P<prefix>[A-Za-z0-9_-]+)_(?P<number>[0-9]{4})_(?P<name>[A-Za-z0-9.-]+)_(?P<category>[A-Z0-9-]+)")
LEGACY_IDENTITY = re.compile(r"(?P<prefix>[A-Za-z0-9_-]+)_(?P<number>[0-9]{4})_(?P<category>[A-Z0-9-]+)")


def match_identity(value: str):
    if len(value) > 255:
        return None
    match = NAMED_IDENTITY.fullmatch(value) or LEGACY_IDENTITY.fullmatch(value)
    if match is None or int(match["number"]) == 0:
        return None
    if "name" in match.re.groupindex and not any(char.isalnum() for char in match["name"]):
        return None
    return match


def base_name(identity: str) -> str:
    match = match_identity(identity)
    return identity.rsplit("_", 1)[0] if match is not None and "name" in match.re.groupindex else identity


def map_bases(identity: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((base_name(identity), identity)))
