"""Object stores: local files and S3 through a stubbed boto3 client (no network)."""

from __future__ import annotations

import io

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from advisor_intel.storage import ForeignURI, LocalObjectStore, ObjectNotFound, S3ObjectStore, open_object_store


async def test_local_store_round_trip(tmp_path):
    s = LocalObjectStore(tmp_path / "objs")
    uri = await s.put_bytes("audio/call-1/rec.wav", b"RIFF....")
    assert uri == f"file://{tmp_path / 'objs' / 'audio/call-1/rec.wav'}"
    assert await s.get_bytes(uri) == b"RIFF...."
    assert await s.get_bytes("audio/call-1/rec.wav") == b"RIFF...."
    assert await s.exists(uri) and not await s.exists("audio/call-1/none.wav")
    await s.put_json("profiles/run-a/v1.json", {"a": 1})
    assert await s.get_json("profiles/run-a/v1.json") == {"a": 1}
    assert await s.list("audio/") == ["audio/call-1/rec.wav"]
    assert await s.list() == ["audio/call-1/rec.wav", "profiles/run-a/v1.json"]
    await s.delete(uri)
    with pytest.raises(ObjectNotFound):
        await s.get_bytes(uri)
    assert await s.url("profiles/run-a/v1.json") is None


async def test_local_store_refuses_foreign_and_unsafe_paths(tmp_path):
    s = LocalObjectStore(tmp_path / "objs")
    (tmp_path / "secret.txt").write_text("nope")
    for bad in (f"file://{tmp_path / 'secret.txt'}", "s3://bucket/key", "../secret.txt", "a/../../secret.txt"):
        with pytest.raises((ForeignURI, ValueError)):
            await s.get_bytes(bad)
    assert not s.owns(f"file://{tmp_path / 'secret.txt'}") and s.owns("audio/x.wav")


def _s3():
    client = boto3.client("s3", region_name="af-south-1", aws_access_key_id="test", aws_secret_access_key="test")
    return client, Stubber(client)


async def test_s3_store_against_stubbed_client():
    client, stub = _s3()
    s = S3ObjectStore("advisor-intel", "prod", client=client)
    assert s.uri_for("audio/c1/rec.wav") == "s3://advisor-intel/prod/audio/c1/rec.wav"
    stub.add_response("put_object", {}, {"Bucket": "advisor-intel", "Key": "prod/audio/c1/rec.wav", "Body": b"wav", "ContentType": "audio/wav"})
    body = StreamingBody(io.BytesIO(b"wav"), 3)
    stub.add_response("get_object", {"Body": body}, {"Bucket": "advisor-intel", "Key": "prod/audio/c1/rec.wav"})
    stub.add_client_error("head_object", service_error_code="404", http_status_code=404, expected_params={"Bucket": "advisor-intel", "Key": "prod/missing"})
    stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404, expected_params={"Bucket": "advisor-intel", "Key": "prod/missing"})
    stub.add_response(
        "list_objects_v2",
        {"Contents": [{"Key": "prod/audio/c1/rec.wav"}], "IsTruncated": True, "NextContinuationToken": "t1"},
        {"Bucket": "advisor-intel", "Prefix": "prod/audio/"},
    )
    stub.add_response(
        "list_objects_v2", {"Contents": [{"Key": "prod/audio/c2/rec.wav"}], "IsTruncated": False}, {"Bucket": "advisor-intel", "Prefix": "prod/audio/", "ContinuationToken": "t1"}
    )
    stub.add_response("delete_object", {}, {"Bucket": "advisor-intel", "Key": "prod/audio/c1/rec.wav"})
    with stub:
        uri = await s.put_bytes("audio/c1/rec.wav", b"wav", content_type="audio/wav")
        assert await s.get_bytes(uri) == b"wav"
        assert await s.exists("missing") is False
        with pytest.raises(ObjectNotFound):
            await s.get_bytes("missing")
        assert await s.list("audio/") == ["audio/c1/rec.wav", "audio/c2/rec.wav"]
        await s.delete(uri)
    stub.assert_no_pending_responses()
    url = await s.url(uri, expires_s=600)  # presigning is local: no request is made
    assert url.startswith("https://advisor-intel.s3.") and "prod/audio/c1/rec.wav" in url
    assert "X-Amz-Expires=600" in url or "Expires=" in url
    with pytest.raises(ForeignURI):
        s.key_for("s3://other-bucket/prod/x")
    with pytest.raises(ForeignURI):
        s.key_for("s3://advisor-intel/staging/x")


async def test_s3_put_json_sends_json(monkeypatch):
    client, stub = _s3()
    s = S3ObjectStore("b", client=client)
    stub.add_response("put_object", {}, {"Bucket": "b", "Key": "k.json", "Body": ANY, "ContentType": "application/json"})
    with stub:
        assert await s.put_json("k.json", {"x": 1}) == "s3://b/k.json"


def test_open_object_store(tmp_path):
    assert isinstance(open_object_store(f"file://{tmp_path}/o"), LocalObjectStore)
    client, _ = _s3()
    s3 = open_object_store("s3://bucket/some/prefix", s3_client=client)
    assert isinstance(s3, S3ObjectStore) and s3.uri_for("a") == "s3://bucket/some/prefix/a"
    with pytest.raises(ValueError):
        open_object_store("gs://bucket")
