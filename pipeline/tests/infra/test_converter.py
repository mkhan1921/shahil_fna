"""The data converter: Pydantic payloads and payload offload to object storage (spec §13.1)."""

from __future__ import annotations

import io
from datetime import date

import pytest
from temporalio.converter import StorageDriverStoreContext, StorageDriverWorkflowInfo

from advisor_intel.models.runs import CallArgs, Stage, VersionPins
from advisor_intel.models.transcript import Segment, Transcript, Word
from advisor_intel.storage import LocalObjectStore, S3ObjectStore
from advisor_intel.workflows.contracts import StageInput
from advisor_intel.workflows.converter import ObjectStoreDriver, PayloadIntegrityError, data_converter


def _big_transcript(n: int = 400) -> Transcript:
    segs = [
        Segment(
            segment_id=f"s{i:04d}",
            speaker_label="spk_1",
            speaker_role="client_primary",
            t_start=i,
            t_end=i + 0.9,
            words=[Word(text="sixty", t_start=i, t_end=i + 0.5, conf=0.9, alts=[("fifty", 0.1)])],
            text="I want to retire at sixty",
        )
        for i in range(n)
    ]
    return Transcript(call_id="c1", anchor_date=date(2026, 10, 15), segments=segs, asr_provider="fake", asr_model_version="fake-1")


async def test_small_payloads_stay_inline_and_round_trip(tmp_path):
    objects = LocalObjectStore(tmp_path / "o")
    conv = data_converter(objects, threshold=4096)
    args = StageInput(run_id="c1-run-a", args=CallArgs(call_id="c1", start_stage=Stage.AGENT, versions=VersionPins(cma_version="2027Q1")))
    [payload] = await conv.encode([args])
    assert payload.metadata["encoding"] == b"json/plain"
    assert await conv.decode([payload], [StageInput]) == [args]
    assert await objects.list() == []


async def test_large_payloads_are_offloaded_and_restored(tmp_path):
    objects = LocalObjectStore(tmp_path / "o")
    conv = data_converter(objects, threshold=4096)
    t = _big_transcript()
    [payload] = await conv.encode([t])
    assert payload.ByteSize() < 1024 and payload.metadata["encoding"] == b"json/protobuf"
    stored = await objects.list("temporal-payloads")
    assert len(stored) == 1 and "/sha256/" in stored[0]
    [back] = await conv.decode([payload], [Transcript])
    assert back == t
    # Same bytes, same object (content-addressed): encoding again stores nothing new.
    await conv.encode([t])
    assert await objects.list("temporal-payloads") == stored


async def test_tampered_payload_is_refused(tmp_path):
    objects = LocalObjectStore(tmp_path / "o")
    conv = data_converter(objects, threshold=1024)
    [payload] = await conv.encode([_big_transcript(50)])
    [key] = await objects.list("temporal-payloads")
    await objects.put_bytes(key, b"tampered")
    with pytest.raises(PayloadIntegrityError):
        await conv.decode([payload], [Transcript])


async def test_payloads_are_scoped_by_workflow(tmp_path):
    objects = LocalObjectStore(tmp_path / "o")
    driver = ObjectStoreDriver(objects)
    from temporalio.api.common.v1 import Payload

    ctx = StorageDriverStoreContext(target=StorageDriverWorkflowInfo(namespace="advisor-intel", id="c1-run-a", type="CallPipelineWorkflow"))
    [claim] = await driver.store(ctx, [Payload(data=b"x" * 10)])
    assert "/ns/advisor-intel/wf/c1-run-a/sha256/" in claim.claim_data["uri"]
    [p] = await driver.retrieve(None, [claim])  # type: ignore[arg-type]
    assert p.data == b"x" * 10


class FakeS3:
    """Just enough of the boto3 S3 client for the driver."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket, Key, Body, **_):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            e = Exception("404")
            e.response = {"Error": {"Code": "404"}}  # type: ignore[attr-defined]
            raise e
        return {}


async def test_offload_to_s3():
    s3 = FakeS3()
    conv = data_converter(S3ObjectStore("payloads", "temporal", client=s3), threshold=2048)
    t = _big_transcript(100)
    [payload] = await conv.encode([t])
    assert next(k for (_, k) in s3.objects).startswith("temporal/temporal-payloads/v1/")
    assert (await conv.decode([payload], [Transcript]))[0] == t


def test_without_object_store_it_is_the_plain_pydantic_converter():
    from temporalio.contrib.pydantic import pydantic_data_converter

    assert data_converter(None) is pydantic_data_converter
