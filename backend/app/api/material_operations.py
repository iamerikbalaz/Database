from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PureWindowsPath
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.resources import SessionDatabase
from app.db.models import (
    MaterialWorkflowStatus,
    PBRMaterial,
    PBRMaterialMetadata,
    PBRMaterialMetadataSnapshot,
)
from app.schemas import (
    MaterialFolderLinkRead,
    MaterialFolderPreflightRead,
    MaterialFolderRequest,
    MaterialMarkDoneRead,
    MaterialMarkDoneRequest,
)
from app.worker_client import (
    MaterialPreflightClient,
    WorkerClientError,
    WorkerMaterialPreflight,
)


@dataclass(frozen=True)
class _MaterialRevision:
    technical_identity: str
    folder_path: str | None
    workflow_status: str
    updated_at: datetime


def _revision(material: PBRMaterial) -> _MaterialRevision:
    return _MaterialRevision(
        technical_identity=material.technical_identity,
        folder_path=material.folder_path,
        workflow_status=material.workflow_status,
        updated_at=material.updated_at,
    )


def _get_material_revision(
    database: SessionDatabase,
    material_id: UUID,
) -> _MaterialRevision:
    with database.session() as session:
        material = session.get(PBRMaterial, material_id)
        if material is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="PBR material not found.",
            )
        return _revision(material)


def _call_worker(
    worker_client: MaterialPreflightClient,
    folder_path: str,
) -> WorkerMaterialPreflight:
    try:
        return worker_client.preflight(folder_path)
    except WorkerClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def _public_preflight(
    result: WorkerMaterialPreflight,
    expected_identity: str,
) -> MaterialFolderPreflightRead:
    identity_matches = result.folder_name == expected_identity
    public_result = {
        "schema_version": result.schema_version,
        "folder_name": result.folder_name,
        "master_resolution": result.master_resolution,
        "policy": result.policy,
        "metadata_status": result.metadata_status,
        "source_filename": result.source_filename,
        "sha256": result.sha256,
        "hex_color": result.hex_color,
        "width_cm": result.width_cm,
        "height_cm": result.height_cm,
        # Metadata errors are non-blocking in the worker contract, so expose them
        # with the other metadata findings rather than as top-level safety errors.
        "warnings": [
            *[item.model_dump() for item in result.metadata_warnings],
            *[item.model_dump() for item in result.metadata_errors],
            *[item.model_dump() for item in result.warnings],
        ],
        "errors": [item.model_dump() for item in result.errors],
        "can_continue": result.can_continue,
    }
    for finding in [*public_result["warnings"], *public_result["errors"]]:
        finding_path = finding.get("path")
        if isinstance(finding_path, str) and (
            finding_path.startswith(("/", "\\"))
            or PureWindowsPath(finding_path).is_absolute()
        ):
            finding["path"] = None
    public_result["identity_matches"] = identity_matches
    if not identity_matches:
        public_result["can_continue"] = False
        public_result["errors"] = [
            *public_result["errors"],
            {
                "code": "TECHNICAL_IDENTITY_MISMATCH",
                "message": "Worker folder_name does not match material technical_identity.",
                "expected_technical_identity": expected_identity,
                "actual_folder_name": result.folder_name,
            },
        ]
    return MaterialFolderPreflightRead.model_validate(public_result)


def _require_safe_preflight(
    result: WorkerMaterialPreflight,
    expected_identity: str,
) -> None:
    if not result.can_continue:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "MATERIAL_FOLDER_PREFLIGHT_FAILED",
                "message": "The material folder did not pass the worker safety checks.",
                "preflight": _public_preflight(result, expected_identity).model_dump(
                    mode="json"
                ),
            },
        )


def _require_matching_identity(
    result: WorkerMaterialPreflight,
    expected_identity: str,
) -> None:
    if result.folder_name != expected_identity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Worker folder_name does not match material technical_identity.",
        )


def _require_unchanged_revision(
    material: PBRMaterial,
    expected: _MaterialRevision,
) -> None:
    if _revision(material) != expected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="PBR material changed while folder preflight was running.",
        )


def _metadata_values(result: WorkerMaterialPreflight, loaded_at: datetime) -> dict[str, Any]:
    return {
        "status": result.metadata_status.value,
        "source_filename": result.source_filename,
        "source_sha256": result.sha256,
        "source_content": result.raw_content,
        "hex_color": result.hex_color,
        "width_cm": result.width_cm,
        "height_cm": result.height_cm,
        "master_resolution": result.master_resolution,
        "warnings": [
            *[warning.model_dump() for warning in result.metadata_warnings],
            *[error.model_dump() for error in result.metadata_errors],
        ],
        "loaded_at": loaded_at,
    }


def build_material_operations_router(
    database: SessionDatabase,
    worker_client: MaterialPreflightClient,
) -> APIRouter:
    router = APIRouter(prefix="/api/materials", tags=["materials"])

    @router.post(
        "/{material_id}/folder-preflight",
        response_model=MaterialFolderPreflightRead,
    )
    def folder_preflight(
        material_id: UUID,
        payload: MaterialFolderRequest,
    ) -> MaterialFolderPreflightRead:
        expected = _get_material_revision(database, material_id)
        return _public_preflight(
            _call_worker(worker_client, payload.folder_path),
            expected.technical_identity,
        )

    @router.post(
        "/{material_id}/folder-link",
        response_model=MaterialFolderLinkRead,
    )
    def folder_link(
        material_id: UUID,
        payload: MaterialFolderRequest,
    ) -> MaterialFolderLinkRead:
        expected = _get_material_revision(database, material_id)
        preflight = _call_worker(worker_client, payload.folder_path)
        _require_matching_identity(preflight, expected.technical_identity)
        _require_safe_preflight(preflight, expected.technical_identity)

        with database.session() as session:
            material = session.scalar(
                select(PBRMaterial)
                .where(PBRMaterial.id == material_id)
                .with_for_update()
            )
            if material is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="PBR material not found.",
                )
            _require_unchanged_revision(material, expected)
            _require_matching_identity(preflight, material.technical_identity)
            material.folder_path = payload.folder_path
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="folder_path is already linked to another material.",
                ) from exc
            session.refresh(material)
            return MaterialFolderLinkRead(
                material=material,
                preflight=_public_preflight(preflight, material.technical_identity),
            )

    @router.post(
        "/{material_id}/mark-done",
        response_model=MaterialMarkDoneRead,
    )
    def mark_done(
        material_id: UUID,
        payload: MaterialMarkDoneRequest | None = None,
    ) -> MaterialMarkDoneRead:
        del payload
        expected = _get_material_revision(database, material_id)
        if expected.workflow_status == MaterialWorkflowStatus.DONE.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="PBR material is already DONE.",
            )
        if expected.folder_path is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="PBR material does not have a linked folder.",
            )

        preflight = _call_worker(worker_client, expected.folder_path)
        _require_matching_identity(preflight, expected.technical_identity)
        _require_safe_preflight(preflight, expected.technical_identity)
        loaded_at = datetime.now(UTC)

        with database.session() as session:
            material = session.scalar(
                select(PBRMaterial)
                .where(PBRMaterial.id == material_id)
                .with_for_update()
            )
            if material is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="PBR material not found.",
                )
            _require_unchanged_revision(material, expected)
            if material.workflow_status == MaterialWorkflowStatus.DONE.value:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="PBR material is already DONE.",
                )
            if material.folder_path != expected.folder_path:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="PBR material folder changed while preflight was running.",
                )
            _require_matching_identity(preflight, material.technical_identity)

            current = session.get(PBRMaterialMetadata, material_id)
            if current is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="PBR material metadata state is missing.",
                )
            last_sequence = session.scalar(
                select(func.max(PBRMaterialMetadataSnapshot.sequence_number)).where(
                    PBRMaterialMetadataSnapshot.material_id == material_id
                )
            )
            values = _metadata_values(preflight, loaded_at)
            snapshot = PBRMaterialMetadataSnapshot(
                material_id=material_id,
                sequence_number=(last_sequence or 0) + 1,
                **values,
            )
            try:
                session.add(snapshot)
                session.flush()
                current.current_snapshot_id = snapshot.id
                for field_name, value in values.items():
                    setattr(current, field_name, value)
                material.workflow_status = MaterialWorkflowStatus.DONE.value
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Concurrent material completion conflicted with this request.",
                ) from exc
            session.refresh(material)
            session.refresh(current)
            session.refresh(snapshot)
            return MaterialMarkDoneRead(
                material=material,
                metadata=current,
                snapshot=snapshot,
                preflight=_public_preflight(preflight, material.technical_identity),
            )

    return router
