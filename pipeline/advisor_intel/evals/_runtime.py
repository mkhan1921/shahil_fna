"""Small helpers shared by the evaluation modules: async bridging, lazy imports, hashing and local paths."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import importlib
import inspect
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from pydantic import BaseModel

from ..config import PIPELINE_DIR

T = TypeVar("T")

DATA_DIR_ENV = "ADVISOR_INTEL_DATA_DIR"
PROMPTS_DIR_ENV = "ADVISOR_INTEL_PROMPTS_DIR"


def data_dir() -> Path:
    """Root for local datasets, caches and reports (gitignored ``pipeline/.data`` unless overridden)."""
    return Path(os.environ.get(DATA_DIR_ENV) or (PIPELINE_DIR / ".data"))


def prompts_dir() -> Path:
    """The versioned prompt files (``prompts/<component>/v{n}.md``, §7.2, §15.6)."""
    return Path(os.environ.get(PROMPTS_DIR_ENV) or (PIPELINE_DIR / "advisor_intel" / "prompts"))


def braintrust_enabled() -> bool:
    """Braintrust paths activate only with an API key; everything else runs on local files."""
    return bool(os.environ.get("BRAINTRUST_API_KEY"))


def run_sync(value: Awaitable[T] | T) -> T:
    """Resolve a value that may be awaitable, from sync code, whether or not an event loop is running."""
    if not inspect.isawaitable(value):
        return value  # type: ignore[return-value]

    async def _wrap() -> T:
        return await value  # type: ignore[misc]

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_wrap())
    # Called from inside a running loop (e.g. a Braintrust task thread or a notebook): run on a fresh loop.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _wrap()).result()


def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a sync or async callable and return its result synchronously."""
    return run_sync(fn(*args, **kwargs))


def lazy_module(module: str) -> Any | None:
    """The module if it imports, else None (other stages are built in parallel)."""
    try:
        return importlib.import_module(module)
    except ImportError:
        return None


def lazy_attr(module: str, attr: str) -> Any | None:
    """``module.attr`` if the module imports, else None."""
    return getattr(lazy_module(module), attr, None)


def to_jsonable(obj: Any) -> Any:
    """Pydantic models, dates and sets to plain JSON types."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(to_jsonable(v) for v in obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    return obj


def stable_hash(obj: Any, n: int = 16) -> str:
    """A short content hash of any JSON-able object (row ids, cache keys)."""
    blob = json.dumps(to_jsonable(obj), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:n]


def unit_hash(key: str) -> float:
    """A deterministic number in [0, 1) for a key, for splits that must not depend on list order."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2**64


def get(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute or key access, for results that may be models, dataclasses or dicts."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def write_json(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(to_jsonable(obj), indent=2, sort_keys=True))
    tmp.replace(path)
    return path
