"""Independent verification of exact, irreversible local-copy retirement receipts."""
from typing import Annotated, Literal

from pydantic import Field

from app.material_review import canonical_hash
from app.packaging_contract import Operation, Sha, Size, Strict, Version, _require


class PackagingRetirementCommand(Strict):
    retirement_id: Operation
    proof_sha256: Sha

    def document(self, prepared):
        return {"schema_version": 1, "operation_id": prepared.request.operation_id,
            "request_hash": prepared.request_hash, "plan_hash": prepared.request.plan_hash,
            **self.model_dump(mode="json")}


class PackagingRetirementReceipt(Strict):
    schema_version: Version
    status: Literal["REMOVED"]
    operation_id: Operation
    request_hash: Sha
    plan_hash: Sha
    proof_sha256: Sha
    retirement_id: Operation
    retirement_request_hash: Sha
    file_count: Annotated[int, Field(ge=2, le=20008)]
    byte_count: Size

    def verify(self, prepared, accepted, command):
        _require(accepted.status == "READY" and accepted.stored is not None
            and accepted.terminal in {"OPEN", "CLOSED"}
            and accepted.operation_id == prepared.request.operation_id
            and accepted.request_hash == prepared.request_hash
            and accepted.stored.plan_hash == prepared.request.plan_hash
            and accepted.stored.proof_sha256 == command.proof_sha256)
        expected = {**command.document(prepared), "status": "REMOVED",
            "retirement_request_hash": canonical_hash(command.document(prepared)),
            "file_count": len(accepted.stored.payload.files),
            "byte_count": sum(item.size for item in accepted.stored.payload.files)}
        _require(self.model_dump(mode="json") == expected)
