"""The data converter for every client and worker: Pydantic payloads plus payload offload (spec §13.1).

Payload offload uses Temporal External Storage (the SDK's claim-check): any payload at or above
the size threshold is written to object storage and only a small reference (driver name, object
URI, SHA-256) enters the workflow history, so a 90-minute call's transcript or a large review
submission never approaches the history size limits. ``ObjectStoreDriver`` stores payloads in
the pipeline's own object store (S3 in production, a local directory in development and tests),
content-addressed and scoped by namespace and workflow or activity, and verifies the hash on read.

Every component that reads payloads (workers, the API's client, the replayer) must be built with
the same driver, or it cannot resolve references.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from collections.abc import Sequence

from temporalio.api.common.v1 import Payload
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.converter import (
    DataConverter,
    ExternalStorage,
    StorageDriver,
    StorageDriverActivityInfo,
    StorageDriverClaim,
    StorageDriverRetrieveContext,
    StorageDriverStoreContext,
    StorageDriverWorkflowInfo,
)

from ..storage.objects import ObjectStore, safe_segment

DEFAULT_OFFLOAD_BYTES = 128 * 1024
DRIVER_NAME = "advisor-intel.objects"


class PayloadIntegrityError(RuntimeError):
    pass


class ObjectStoreDriver(StorageDriver):
    """A Temporal storage driver over an ``ObjectStore`` (local directory or S3)."""

    def __init__(self, objects: ObjectStore, *, prefix: str = "temporal-payloads", name: str = DRIVER_NAME) -> None:
        self.objects = objects
        self.prefix = prefix.strip("/")
        self._name = name

    def name(self) -> str:
        return self._name

    def type(self) -> str:
        return "advisor-intel.object-store"

    def _scope(self, context: StorageDriverStoreContext) -> str:
        target = context.target
        if target is None:
            return "unscoped"
        ns = safe_segment(target.namespace)
        if isinstance(target, StorageDriverWorkflowInfo):
            return f"ns/{ns}/wf/{safe_segment(target.id or 'null')}"
        if isinstance(target, StorageDriverActivityInfo):
            return f"ns/{ns}/act/{safe_segment(target.type or 'null')}/{safe_segment(target.id or 'null')}"
        return f"ns/{ns}"

    async def store(self, context: StorageDriverStoreContext, payloads: Sequence[Payload]) -> list[StorageDriverClaim]:
        scope = self._scope(context)
        claims = []
        for p in payloads:
            data = p.SerializeToString()
            digest = hashlib.sha256(data).hexdigest()
            key = f"{self.prefix}/v1/{scope}/sha256/{digest}"
            if not await self.objects.exists(key):
                await self.objects.put_bytes(key, data, content_type="application/x-protobuf")
            claims.append(StorageDriverClaim(claim_data={"uri": self.objects.uri_for(key), "sha256": digest, "size": str(len(data))}))
        return claims

    async def retrieve(self, context: StorageDriverRetrieveContext, claims: Sequence[StorageDriverClaim]) -> list[Payload]:
        out = []
        for c in claims:
            data = await self.objects.get_bytes(c.claim_data["uri"])
            if hashlib.sha256(data).hexdigest() != c.claim_data.get("sha256"):
                raise PayloadIntegrityError(f"payload at {c.claim_data['uri']} does not match its hash")
            p = Payload()
            p.ParseFromString(data)
            out.append(p)
        return out


def offload_threshold() -> int:
    v = os.environ.get("ADVISOR_INTEL_PAYLOAD_OFFLOAD_BYTES")
    return int(v) if v else DEFAULT_OFFLOAD_BYTES


def data_converter(objects: ObjectStore | None = None, *, threshold: int | None = None) -> DataConverter:
    """Pydantic JSON payloads; with ``objects``, payloads at or above ``threshold`` bytes are offloaded."""
    if objects is None:
        return pydantic_data_converter
    storage = ExternalStorage(
        drivers=[ObjectStoreDriver(objects)], payload_size_threshold=offload_threshold() if threshold is None else threshold
    )
    return dataclasses.replace(pydantic_data_converter, external_storage=storage)
