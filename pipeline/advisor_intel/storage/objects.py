"""Object storage for audio, transcripts, profiles, calibrator artifacts and Temporal payloads (§14.2).

Two backends behind one interface, chosen by ``Settings.object_store_uri``:

- ``file:///path`` → ``LocalObjectStore`` for local runs and tests;
- ``s3://bucket/prefix`` → ``S3ObjectStore`` over boto3 (any S3-compatible service; set
  ``AWS_ENDPOINT_URL`` for a non-AWS endpoint). boto3 is synchronous, so calls run in a thread.

Keys are relative, slash-separated paths (``audio/<call_id>/call.wav``). A URI from another place
(``file://…`` outside the root, another bucket) is refused rather than read, so an API caller
cannot point the pipeline at arbitrary files.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from pydantic import BaseModel


class ObjectNotFound(KeyError):
    """The key does not exist in the store."""


class ForeignURI(ValueError):
    """A URI outside this store (another bucket, a file outside the root)."""


def _check_key(key: str) -> str:
    key = key.lstrip("/")
    parts = key.split("/")
    if not key or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"invalid object key {key!r}")
    return key


def safe_segment(s: str) -> str:
    """A path segment safe for any backend: percent-encode everything outside [A-Za-z0-9._-]."""
    return quote(s, safe="-_.") or "_"


class ObjectStore(ABC):
    """Bytes by key, with JSON helpers and URIs that identify an object across processes."""

    base_uri: str

    @abstractmethod
    def uri_for(self, key: str) -> str: ...

    @abstractmethod
    def key_for(self, uri_or_key: str) -> str:
        """The key for a URI of this store (or a bare key). Raises ``ForeignURI`` otherwise."""

    @abstractmethod
    async def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        """Store ``data`` under ``key`` (atomically replacing it) and return its URI."""

    @abstractmethod
    async def get_bytes(self, uri_or_key: str) -> bytes: ...

    @abstractmethod
    async def exists(self, uri_or_key: str) -> bool: ...

    @abstractmethod
    async def delete(self, uri_or_key: str) -> None: ...

    @abstractmethod
    async def list(self, prefix: str = "") -> list[str]:
        """Keys under ``prefix``, sorted."""

    @abstractmethod
    async def url(self, uri_or_key: str, *, expires_s: int = 3600) -> str | None:
        """A time-limited URL a browser can fetch (S3 presigned GET), or None when the backend has none."""

    async def put_json(self, key: str, obj: Any) -> str:
        if isinstance(obj, BaseModel):
            data = obj.model_dump_json().encode()
        else:
            data = json.dumps(obj, separators=(",", ":"), default=str).encode()
        return await self.put_bytes(key, data, content_type="application/json")

    async def get_json(self, uri_or_key: str) -> Any:
        return json.loads(await self.get_bytes(uri_or_key))

    async def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> str:
        data = await asyncio.to_thread(Path(path).read_bytes)
        return await self.put_bytes(key, data, content_type=content_type)

    async def download(self, uri_or_key: str, dest: Path) -> Path:
        data = await self.get_bytes(uri_or_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(dest.write_bytes, data)
        return dest

    def owns(self, uri_or_key: str) -> bool:
        try:
            self.key_for(uri_or_key)
            return True
        except (ForeignURI, ValueError):
            return False


class LocalObjectStore(ObjectStore):
    """Files under a root directory; URIs are ``file://`` paths."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_uri = f"file://{self.root}"

    def uri_for(self, key: str) -> str:
        return f"file://{self.root / _check_key(key)}"

    def key_for(self, uri_or_key: str) -> str:
        if not uri_or_key.startswith("file://"):
            if "://" in uri_or_key:
                raise ForeignURI(uri_or_key)
            return _check_key(uri_or_key)
        path = Path(uri_or_key.removeprefix("file://")).resolve()
        try:
            rel = path.relative_to(self.root)
        except ValueError as e:
            raise ForeignURI(f"{uri_or_key} is outside {self.base_uri}") from e
        return _check_key(rel.as_posix())

    def _path(self, uri_or_key: str) -> Path:
        return self.root / self.key_for(uri_or_key)

    async def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise

        await asyncio.to_thread(write)
        return self.uri_for(key)

    async def get_bytes(self, uri_or_key: str) -> bytes:
        path = self._path(uri_or_key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as e:
            raise ObjectNotFound(uri_or_key) from e

    async def exists(self, uri_or_key: str) -> bool:
        return self._path(uri_or_key).is_file()

    async def delete(self, uri_or_key: str) -> None:
        self._path(uri_or_key).unlink(missing_ok=True)

    async def list(self, prefix: str = "") -> list[str]:
        if ".." in prefix.split("/"):
            raise ValueError(f"invalid prefix {prefix!r}")
        base = self.root / prefix.lstrip("/") if prefix else self.root

        def walk() -> list[str]:
            start = base if base.is_dir() else base.parent
            out = []
            for p in start.rglob("*"):
                if p.is_file() and not p.name.startswith("."):
                    rel = p.relative_to(self.root).as_posix()
                    if rel.startswith(prefix.lstrip("/")):
                        out.append(rel)
            return sorted(out)

        return await asyncio.to_thread(walk)

    async def url(self, uri_or_key: str, *, expires_s: int = 3600) -> str | None:
        return None

    async def download(self, uri_or_key: str, dest: Path) -> Path:
        src = self._path(uri_or_key)
        if not src.is_file():
            raise ObjectNotFound(uri_or_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, src, dest)
        return dest

    async def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> str:
        dest = self._path(key)

        def copy() -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
            os.close(fd)
            try:
                shutil.copyfile(path, tmp)
                os.replace(tmp, dest)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise

        await asyncio.to_thread(copy)
        return self.uri_for(key)


class S3ObjectStore(ObjectStore):
    """Objects in one S3 bucket under an optional prefix; URIs are ``s3://bucket/prefix/key``."""

    def __init__(
        self, bucket: str, prefix: str = "", *, client: Any | None = None, endpoint_url: str | None = None, region_name: str | None = None
    ) -> None:
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region_name)
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.base_uri = f"s3://{bucket}/{self.prefix}".rstrip("/")

    def _object_key(self, key: str) -> str:
        return self.prefix + _check_key(key)

    def uri_for(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._object_key(key)}"

    def key_for(self, uri_or_key: str) -> str:
        if not uri_or_key.startswith("s3://"):
            if "://" in uri_or_key:
                raise ForeignURI(uri_or_key)
            return _check_key(uri_or_key)
        u = urlparse(uri_or_key)
        path = u.path.lstrip("/")
        if u.netloc != self.bucket or not path.startswith(self.prefix):
            raise ForeignURI(f"{uri_or_key} is outside {self.base_uri}")
        return _check_key(path[len(self.prefix) :])

    async def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        extra = {"ContentType": content_type} if content_type else {}
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=self._object_key(key), Body=data, **extra)
        return self.uri_for(key)

    async def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> str:
        """Upload a file with boto3's managed transfer (multipart for large recordings, nothing held in memory)."""
        extra = {"ExtraArgs": {"ContentType": content_type}} if content_type else {}
        await asyncio.to_thread(self.client.upload_file, str(path), self.bucket, self._object_key(key), **extra)
        return self.uri_for(key)

    async def get_bytes(self, uri_or_key: str) -> bytes:
        key = self._object_key(self.key_for(uri_or_key))

        def read() -> bytes:
            try:
                resp = self.client.get_object(Bucket=self.bucket, Key=key)
            except Exception as e:  # botocore ClientError: map NoSuchKey to ObjectNotFound
                if _is_not_found(e):
                    raise ObjectNotFound(uri_or_key) from e
                raise
            body = resp["Body"]
            try:
                return body.read()
            finally:
                close = getattr(body, "close", None)
                if close:
                    close()

        return await asyncio.to_thread(read)

    async def exists(self, uri_or_key: str) -> bool:
        key = self._object_key(self.key_for(uri_or_key))

        def head() -> bool:
            try:
                self.client.head_object(Bucket=self.bucket, Key=key)
                return True
            except Exception as e:
                if _is_not_found(e):
                    return False
                raise

        return await asyncio.to_thread(head)

    async def delete(self, uri_or_key: str) -> None:
        key = self._object_key(self.key_for(uri_or_key))
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    async def list(self, prefix: str = "") -> list[str]:
        full = self.prefix + prefix.lstrip("/")

        def walk() -> list[str]:
            out: list[str] = []
            token: str | None = None
            while True:
                kw: dict[str, Any] = {"Bucket": self.bucket, "Prefix": full}
                if token:
                    kw["ContinuationToken"] = token
                resp = self.client.list_objects_v2(**kw)
                out.extend(o["Key"][len(self.prefix) :] for o in resp.get("Contents", []))
                if not resp.get("IsTruncated"):
                    return sorted(out)
                token = resp.get("NextContinuationToken")

        return await asyncio.to_thread(walk)

    async def url(self, uri_or_key: str, *, expires_s: int = 3600) -> str | None:
        key = self._object_key(self.key_for(uri_or_key))
        return await asyncio.to_thread(
            self.client.generate_presigned_url, "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_s
        )


def _is_not_found(e: Exception) -> bool:
    resp = getattr(e, "response", None)
    if isinstance(resp, dict):
        code = str(resp.get("Error", {}).get("Code", ""))
        return code in ("404", "NoSuchKey", "NotFound")
    return False


def open_object_store(uri: str, *, s3_client: Any | None = None) -> ObjectStore:
    """The store for ``Settings.object_store_uri``."""
    if uri.startswith("file://"):
        return LocalObjectStore(Path(uri.removeprefix("file://")))
    if uri.startswith("s3://"):
        u = urlparse(uri)
        if not u.netloc:
            raise ValueError(f"object store URI {uri!r} names no bucket")
        return S3ObjectStore(u.netloc, u.path, client=s3_client, endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None)
    if "://" not in uri:
        return LocalObjectStore(Path(uri))
    raise ValueError(f"unsupported object store URI {uri!r}: use file:// or s3://")


def new_key(*parts: str, suffix: str = "") -> str:
    """A unique key under the given path segments: ``audio/<call>/<uuid>.wav``."""
    return "/".join([*(safe_segment(p) for p in parts), uuid.uuid4().hex + suffix])
