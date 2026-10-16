"""Temporal Worker Versioning from the first deploy (spec §13.1): workflows stay open for days in review.

Every worker joins the ``advisor-intel`` Worker Deployment under a build id (``ADVISOR_INTEL_BUILD_ID``,
else the CI commit, else a hash of the loaded code). ``CallPipelineWorkflow`` is AUTO_UPGRADE, so a
call waiting for review moves to the new build; every worker change must replay recorded
histories (CI replay test). The short workflows are PINNED to the build they started on.

A new build receives work only once it is the deployment's current version: the deploy pipeline
sets it (``temporal worker deployment set-current-version``), or the worker does at start-up when
``ADVISOR_INTEL_SET_CURRENT_VERSION=true`` (single-build environments, development).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path

from temporalio.api.workflowservice.v1 import DescribeWorkerDeploymentRequest, SetWorkerDeploymentCurrentVersionRequest
from temporalio.client import Client
from temporalio.common import VersioningBehavior, WorkerDeploymentVersion
from temporalio.service import RPCError
from temporalio.worker import WorkerDeploymentConfig

from .. import __version__

DEPLOYMENT_NAME = "advisor-intel"


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def code_hash() -> str:
    """A hash of the package's source (Python, SQL, YAML): changes whenever the worker's code does."""
    root = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.suffix in (".py", ".sql", ".yaml") and "__pycache__" not in p.parts):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def build_id() -> str:
    explicit = os.environ.get("ADVISOR_INTEL_BUILD_ID") or os.environ.get("GITHUB_SHA") or os.environ.get("GIT_SHA")
    if explicit:
        return re.sub(r"[^A-Za-z0-9_-]", "-", explicit)[:64]
    return f"{__version__.replace('.', '-')}-{code_hash()[:12]}"


def deployment_version(build: str | None = None) -> WorkerDeploymentVersion:
    return WorkerDeploymentVersion(deployment_name=os.environ.get("ADVISOR_INTEL_DEPLOYMENT_NAME", DEPLOYMENT_NAME), build_id=build or build_id())


def deployment_config(*, enabled: bool | None = None, build: str | None = None) -> WorkerDeploymentConfig | None:
    """The worker's deployment config, or None when versioning is switched off (``ADVISOR_INTEL_WORKER_VERSIONING=false``)."""
    if not (_flag("ADVISOR_INTEL_WORKER_VERSIONING", True) if enabled is None else enabled):
        return None
    return WorkerDeploymentConfig(version=deployment_version(build), use_worker_versioning=True, default_versioning_behavior=VersioningBehavior.PINNED)


async def wait_for_version(client: Client, version: WorkerDeploymentVersion, *, timeout_s: float = 30.0) -> None:
    """Wait until a polling worker has registered ``version`` with the server."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        try:
            d = await client.workflow_service.describe_worker_deployment(
                DescribeWorkerDeploymentRequest(namespace=client.namespace, deployment_name=version.deployment_name)
            )
            if any(s.deployment_version.build_id == version.build_id or s.version == version.to_canonical_string() for s in d.worker_deployment_info.version_summaries):
                return
        except RPCError:
            pass  # the deployment does not exist until the first poll
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(f"worker deployment version {version.to_canonical_string()} did not register within {timeout_s}s")
        await asyncio.sleep(0.2)


async def set_current_version(client: Client, version: WorkerDeploymentVersion, *, identity: str = "advisor-intel-worker", wait_s: float = 30.0) -> None:
    """Route new and AUTO_UPGRADE workflow tasks to ``version``."""
    await wait_for_version(client, version, timeout_s=wait_s)
    await client.workflow_service.set_worker_deployment_current_version(
        SetWorkerDeploymentCurrentVersionRequest(
            namespace=client.namespace, deployment_name=version.deployment_name, build_id=version.build_id, identity=identity
        )
    )


def should_set_current_version() -> bool:
    return _flag("ADVISOR_INTEL_SET_CURRENT_VERSION", False)
