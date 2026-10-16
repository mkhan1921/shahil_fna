"""The Temporal worker entrypoint (spec §13.1, §14.1): ``advisor-intel-worker``.

One worker process runs every workflow and activity on ``Settings.task_queue``:

- the data converter: Pydantic payloads plus payload offload to object storage;
- the OpenTelemetry interceptor (on the client, so the worker inherits it) and OTLP export to Braintrust;
- Worker Versioning: the ``advisor-intel`` deployment under this build's id (``ADVISOR_INTEL_BUILD_ID``);
- the nightly and monthly Schedules, created or updated at start-up (``ADVISOR_INTEL_ENSURE_SCHEDULES``).

Environment (besides ``config.Settings``): ``ADVISOR_INTEL_MIGRATE_ON_START``, ``ADVISOR_INTEL_SET_CURRENT_VERSION``,
``ADVISOR_INTEL_BUILD_ID``, ``ADVISOR_INTEL_ENSURE_SCHEDULES``, ``ADVISOR_INTEL_MAX_CONCURRENT_ACTIVITIES``, ``ADVISOR_INTEL_LOG_LEVEL``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from datetime import timedelta
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker, WorkerDeploymentConfig

from .activities import NightlyActivities, PipelineActivities, StageFunctions
from .activities.providers import Providers
from .config import Settings, get_settings
from .notify import Notifier
from .storage.objects import ObjectStore, open_object_store
from .storage.store import Store
from .workflows import ALL_WORKFLOWS
from .workflows.client import connect_temporal
from .workflows.schedules import ensure_schedules
from .workflows.versioning import deployment_config, set_current_version, should_set_current_version

log = logging.getLogger("advisor_intel.worker")


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def build_worker(
    client: Client,
    settings: Settings,
    *,
    store: Store,
    objects: ObjectStore,
    notifier: Notifier | Any | None = None,
    stages: StageFunctions | None = None,
    providers: Providers | None = None,
    note_model: Any = None,
    deployment: WorkerDeploymentConfig | None = None,
    task_queue: str | None = None,
) -> Worker:
    """A worker with every workflow and activity, sharing one store, object store and set of stage entry points.

    ``deployment`` defaults to this build's Worker Deployment version (the workflows need a versioned worker).
    """
    stages = stages or StageFunctions()
    pipeline = PipelineActivities(store=store, objects=objects, settings=settings, notifier=notifier, stages=stages, providers=providers, note_model=note_model)
    nightly = NightlyActivities(store=store, objects=objects, settings=settings, stages=stages)
    return Worker(
        client,
        task_queue=task_queue or settings.task_queue,
        workflows=ALL_WORKFLOWS,
        activities=[*pipeline.activities(), *nightly.activities()],
        deployment_config=deployment or deployment_config(),
        max_concurrent_activities=int(os.environ.get("ADVISOR_INTEL_MAX_CONCURRENT_ACTIVITIES", "20")),
        graceful_shutdown_timeout=timedelta(seconds=30),
    )


async def run_worker(settings: Settings | None = None, *, stop: asyncio.Event | None = None) -> None:
    """Connect, register, and poll until SIGINT/SIGTERM (or ``stop``)."""
    from .telemetry import setup_telemetry, shutdown_telemetry

    settings = settings or get_settings()
    setup_telemetry(settings, service_name="advisor-intel-worker")
    if _flag("ADVISOR_INTEL_MIGRATE_ON_START", False):
        from .storage.migrate import migrate

        applied = await migrate(settings.database_url)
        log.info("applied migrations %s", applied or "none (up to date)")
    store = Store.from_dsn(settings.database_url)
    objects = open_object_store(settings.object_store_uri)
    client = await connect_temporal(settings, objects=objects)
    deployment = deployment_config()
    worker = build_worker(client, settings, store=store, objects=objects, deployment=deployment)
    if _flag("ADVISOR_INTEL_ENSURE_SCHEDULES", True):
        log.info("schedules: %s", await ensure_schedules(client, settings.task_queue))
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    try:
        async with worker:
            log.info("worker deployment version %s", deployment.version.to_canonical_string())
            if should_set_current_version(settings.env):
                await set_current_version(client, deployment.version)
                log.info("set %s as the current version", deployment.version.to_canonical_string())
            log.info("polling %s on %s (namespace %s)", settings.task_queue, settings.temporal_address, settings.temporal_namespace)
            await stop.wait()
    finally:
        await store.close()
        shutdown_telemetry()


def main() -> None:
    logging.basicConfig(level=os.environ.get("ADVISOR_INTEL_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
