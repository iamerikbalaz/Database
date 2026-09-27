"""One-level, stat-only source browsing; never download or hash source textures."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.discovery_client import WorkerDiscoveryClient, validate_parent_path


class FolderEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=2048)
    kind: Literal["directory", "file"]
    size: int = Field(ge=0, le=64 * 1024**3)


class FolderContents(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: int = Field(ge=1, le=1)
    parent_path: str = Field(max_length=2048)
    entries: list[FolderEntry] = Field(max_length=4096)
    omitted_entries: int = Field(ge=0, le=4096)

    @model_validator(mode="after")
    def valid(self):
        parent = validate_parent_path(self.parent_path)
        names = set()
        for item in self.entries:
            if (validate_parent_path(item.path) != (*parent, item.name) or item.name in names
                or len(validate_parent_path(item.name)) != 1 or (item.kind == "directory" and item.size != 0)):
                raise ValueError("Invalid directory contents")
            names.add(item.name)
        if len(names) + self.omitted_entries > 4096: raise ValueError("Directory listing too large")
        return self


class WorkerFolderContentsClient(WorkerDiscoveryClient):
    endpoint = "/internal/folder-contents"
    result_type = FolderContents
