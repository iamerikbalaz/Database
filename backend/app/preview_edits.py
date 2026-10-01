"""Explicit, bounded selectors for PNG files directly inside PREVIEW."""
import re
import unicodedata
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator
from app.schemas import ApiSchema, Sha256


def png_basename(value):
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > 255
            or value != value.strip() or value.endswith((".", " "))
            or any(unicodedata.category(char).startswith("C") or char in '/\\:<>"|?*' for char in value)
            or value in {".", ".."} or not value.lower().endswith(".png")
            or not value[:-4] or value[:-4].endswith((".", " "))
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value)):
        raise ValueError("Use a safe PNG filename without a path.")
    return value


class PreviewSelection(ApiSchema):
    id: UUID
    expected_updated_at: datetime


class PreviewEditPlanRequest(ApiSchema):
    materials: list[PreviewSelection] = Field(min_length=1, max_length=100)
    action: Literal["RENAME", "DELETE", "REPLACE", "DELETE_MATCHING", "BULK"]
    filename: str | None = Field(default=None, max_length=255)
    new_name: str | None = Field(default=None, max_length=255)
    find: str | None = Field(default=None, max_length=255)
    replace: str | None = Field(default=None, max_length=255)
    delete_containing: str | None = Field(default=None, max_length=255)
    case_sensitive: bool = True

    @model_validator(mode="after")
    def selection_and_selector(self):
        if len({item.id for item in self.materials}) != len(self.materials):
            raise ValueError("Select each material once.")
        if self.action != "BULK" and self.delete_containing is not None:
            raise ValueError("The combined deletion selector belongs to BULK.")
        if self.action in {"RENAME", "DELETE"}:
            if len(self.materials) != 1 or self.find is not None or self.replace is not None:
                raise ValueError("A single-file action selects one material and an exact filename.")
            png_basename(self.filename)
            if self.action == "RENAME": png_basename(self.new_name)
            elif self.new_name is not None: raise ValueError("Deletion has no new filename.")
        else:
            if self.filename is not None or self.new_name is not None:
                raise ValueError("Bulk actions use literal selectors, not individual filenames.")
            if self.action != "BULK" and not self.find:
                raise ValueError("Bulk actions require a nonempty literal search string.")
            if self.action == "BULK" and (not self.find and not self.delete_containing):
                raise ValueError("Supply a replacement search or a deletion substring.")
            if (self.action == "REPLACE" or (self.action == "BULK" and self.find)) and self.replace is None:
                raise ValueError("Supply the replacement text (which may be empty).")
            if self.action == "BULK" and not self.find and self.replace is not None:
                raise ValueError("Replacement requires a nonempty search string.")
            if self.action == "DELETE_MATCHING" and self.replace is not None:
                raise ValueError("Deletion has no replacement text.")
            for value in (self.find, self.replace, self.delete_containing):
                if value is not None and any(unicodedata.category(char).startswith("C") or char in '/\\:<>"|?*' for char in value):
                    raise ValueError("Use literal filename text without paths or control characters.")
        return self


class PreviewEditApplyRequest(PreviewEditPlanRequest):
    idempotency_key: UUID
    expected_proposal_hash: Sha256
    confirmed: Literal[True]

    @model_validator(mode="after")
    def nonzero_key(self):
        if not self.idempotency_key.int: raise ValueError("Use a nonzero request key.")
        return self


class PreviewEditResume(ApiSchema):
    confirmed: Literal[True]


def changes_for(snapshot, selector):
    files = {item["name"]: item for item in snapshot["files"]}
    issues = []; changes = []
    if selector["action"] in {"RENAME", "DELETE"}:
        if selector["filename"] not in files:
            issues.append({"code": "PREVIEW_FILE_NOT_FOUND", "message": "The exact PNG filename is no longer present."})
        else:
            target = selector["new_name"] if selector["action"] == "RENAME" else None
            if target != selector["filename"]:
                changes.append({"from": selector["filename"], "to": target})
    else:
        flags = 0 if selector["case_sensitive"] else re.IGNORECASE
        pattern = re.compile(re.escape(selector["find"]), flags) if selector.get("find") else None
        deletion = re.compile(re.escape(selector["delete_containing"]), flags) if selector.get("delete_containing") else None
        for name in sorted(files):
            if deletion and deletion.search(name[:-4]): target = None
            elif pattern and pattern.search(name[:-4]):
                target = (pattern.sub(lambda _: selector["replace"], name[:-4]) + name[-4:]
                          if selector["action"] in {"REPLACE", "BULK"} else None)
            else: continue
            if target == name: continue
            if target is not None:
                try: png_basename(target)
                except ValueError:
                    issues.append({"code": "PREVIEW_TARGET_INVALID", "message": f"Replacement would produce an invalid PNG name for {name}."})
                    continue
            changes.append({"from": name, "to": target})
    source_names = {change["from"].casefold() for change in changes}
    occupied = {name.casefold() for name in snapshot["names"]} - source_names
    targets = [change["to"].casefold() for change in changes if change["to"] is not None]
    if len(targets) != len(set(targets)) or any(name in occupied for name in targets):
        issues.append({"code": "PREVIEW_NAME_COLLISION", "message": "A target name would overwrite another file or collide ignoring letter case."})
    return changes, issues
