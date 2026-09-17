"""Minimal publication context and provenance for externally prepared AI drafts.

No provider or source URL is contacted here. Drafts never approve or replace
saved content merely because a provider/model label has been supplied.
"""
import ipaddress
import re
import unicodedata
from datetime import UTC
from typing import Annotated
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from uuid import UUID

from pydantic import BeforeValidator, Field, StringConstraints, field_validator, model_validator
from sqlalchemy import select

from app.catalog import ContentUpdate, Reason, TagValue
from app.db.models import MaterialReviewState, MaterialSourceLink, PublishedBrand
from app.material_review import canonical_hash, material_context
from app.publication_content import draft_view
from app.schemas import ApiSchema
from app.worker_client import Sha256


def source_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError("Invalid source URL")
    if any(char.isspace() or unicodedata.category(char).startswith("C") or char == "\\" for char in unquote(value)):
        raise ValueError("Invalid source URL")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if (parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "?" in value or "#" in value or parsed.port not in {None, 443}):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None or host.endswith((".localhost", ".local", ".internal")) or host == "localhost":
            raise ValueError()
        if (len(host) > 253 or "." not in host or re.fullmatch(r"[a-z][a-z0-9-]{1,62}", host.rsplit(".", 1)[-1]) is None
                or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None for label in host.split("."))):
            raise ValueError()
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        if re.search(r"%(?![0-9a-fA-F]{2})", path): raise ValueError()
        result = urlunsplit(("https", host, path, "", ""))
        if len(result) > 2048: raise ValueError()
        return result
    except (ValueError, UnicodeError):
        raise ValueError("Invalid source URL") from None


def provenance_label(value):
    if not isinstance(value, str) or any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("Invalid provenance label")
    return unicodedata.normalize("NFC", value.strip())


Label = Annotated[str, StringConstraints(min_length=1, max_length=100), BeforeValidator(provenance_label)]


class SourceLinkCreate(ApiSchema):
    idempotency_key: UUID
    url: Annotated[str, BeforeValidator(source_url)]
    reason: Reason


class SourceLinkActivity(ApiSchema):
    idempotency_key: UUID
    expected_version: Annotated[int, Field(strict=True, ge=1)]
    is_active: Annotated[bool, Field(strict=True)]
    reason: Reason


class AiDraftCreate(ApiSchema):
    idempotency_key: UUID
    expected_context_hash: Sha256
    provider: Label
    model: Label
    prompt_version: Label
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=10000)] | None = None
    tags: Annotated[list[TagValue], Field(max_length=100)] = []
    source_link_ids: Annotated[list[UUID], Field(max_length=20)] = []
    reason: Reason

    @field_validator("description")
    @classmethod
    def plain_description(cls, value):
        return ContentUpdate.plain_description(value)

    @field_validator("tags")
    @classmethod
    def canonical_tags(cls, values):
        return ContentUpdate.canonical_tags(values)

    @field_validator("source_link_ids")
    @classmethod
    def canonical_sources(cls, values):
        return sorted(set(values))

    @model_validator(mode="after")
    def has_proposal(self):
        if not self.description and not self.tags:
            raise ValueError("An AI proposal must contain a description or tags")
        return self


class AiDraftAdopt(ApiSchema):
    idempotency_key: UUID
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    expected_context_hash: Sha256
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=10000)] | None = None
    tags: Annotated[list[TagValue], Field(max_length=100)] = []
    reason: Reason

    @field_validator("description")
    @classmethod
    def plain_description(cls, value): return ContentUpdate.plain_description(value)

    @field_validator("tags")
    @classmethod
    def canonical_tags(cls, values): return ContentUpdate.canonical_tags(values)


def source_link_view(item):
    return {"id": str(item.id), "url": item.url, "version": item.version, "is_active": item.is_active}


def publishing_context(session, material):
    content = draft_view(session, material)
    brand = session.scalar(select(PublishedBrand).where(PublishedBrand.id == material.published_brand_id).with_for_update(read=True))
    links = list(session.scalars(select(MaterialSourceLink).where(MaterialSourceLink.material_id == material.id,
        MaterialSourceLink.is_active.is_(True)).order_by(MaterialSourceLink.id)))
    public = {"material_id": str(material.id), "name": material.material_name, "brand": {"id": str(brand.id), "name": brand.name},
        "categories": [{"id": item["id"], "value": item["value"]} for item in content["categories"] if item["is_active"]],
        "collections": [{"id": item["id"], "value": item["value"]} for item in content["collections"] if item["is_active"] and item["brand_id"] == str(brand.id)],
        "source_urls": [{"id": str(item.id), "url": item.url} for item in links]}
    state = session.get(MaterialReviewState, material.id)
    # Private revision inputs are hashed, not returned to an AI caller. Even a
    # change followed by restoring the same public values must stale old drafts.
    digest = canonical_hash({"public": public, "content_revision": content["revision"],
        "material": material_context(material), "material_updated_at": material.updated_at.isoformat(),
        "brand_updated_at": brand.updated_at.isoformat(), "brand_active": brand.is_active,
        "categories": content["categories"], "collections": content["collections"],
        "links": [source_link_view(item) for item in links], "generation": state.generation if state else 0})
    return {"context": public, "context_hash": digest, "content_revision": content["revision"]}


def ai_draft_view(item):
    created = item.created_at if item.created_at.tzinfo else item.created_at.replace(tzinfo=UTC)
    return {"id": str(item.id), "material_id": str(item.material_id), "actor_id": str(item.actor_id), "status": "AI_DRAFT",
        "context_hash": item.context_hash, "content_revision": item.content_revision, "context": item.context,
        "provider": item.provider, "model": item.model, "prompt_version": item.prompt_version,
        "description": item.description, "tags": item.tags, "source_link_ids": item.source_link_ids,
        "reason": item.reason, "created_at": created.isoformat(),
        **({"service_credential_id": str(item.service_credential_id)} if item.service_credential_id else {})}
