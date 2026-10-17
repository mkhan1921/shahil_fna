"""Connecting to Temporal: Temporal Cloud with an API key and TLS, or a local dev server (spec §1.3, §13.1).

Clients and workers share one data converter (Pydantic payloads plus payload offload to object
storage) and the OpenTelemetry interceptor, so traces continue from the API into the workflow.

``start_finetune`` and ``start_rl_training`` start the on-demand training runs (§15.8, §15.9) from
any connected client (an operator's script, the API's admin endpoints).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy

from ..config import Settings
from ..storage.objects import ObjectStore
from .contracts import FinetuneArgs, RlArgs
from .converter import data_converter
from .training import FinetuneExtractorWorkflow, TrainRlExtractorWorkflow

TRAINING_RUN_TIMEOUT = timedelta(days=3)


def training_run_id(kind: str, label: str = "") -> str:
    """``finetune-<label>`` / ``rl-<label>``; the label defaults to the start time, so runs are listed in order."""
    return f"{kind}-{label or datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"


async def start_finetune(
    client: Client, args: FinetuneArgs | None = None, *, task_queue: str, workflow_id: str | None = None
) -> WorkflowHandle:
    """Start ``FinetuneExtractorWorkflow`` (§15.8); the workflow id names the run's artifacts under training/runs/."""
    args = args or FinetuneArgs()
    wf_id = workflow_id or training_run_id("finetune", args.label)
    return await client.start_workflow(
        FinetuneExtractorWorkflow.run,
        args.model_copy(update={"label": args.label or wf_id}),
        id=wf_id,
        task_queue=task_queue,
        execution_timeout=TRAINING_RUN_TIMEOUT,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
    )


async def start_rl_training(
    client: Client, args: RlArgs | None = None, *, task_queue: str, workflow_id: str | None = None
) -> WorkflowHandle:
    """Start ``TrainRlExtractorWorkflow`` (§15.9); it ends at once, with the reasons, unless the start conditions hold."""
    args = args or RlArgs()
    wf_id = workflow_id or training_run_id("rl", args.label)
    return await client.start_workflow(
        TrainRlExtractorWorkflow.run,
        args.model_copy(update={"label": args.label or wf_id}),
        id=wf_id,
        task_queue=task_queue,
        execution_timeout=TRAINING_RUN_TIMEOUT,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
    )


async def connect_temporal(settings: Settings, *, objects: ObjectStore | None = None, interceptors: Sequence[Any] | None = None) -> Client:
    """A client for ``settings.temporal_address`` / ``temporal_namespace``.

    With ``temporal_api_key`` set (Temporal Cloud) the connection uses TLS and the API key;
    ``temporal_tls`` alone enables TLS for a self-hosted server.
    """
    if interceptors is None:
        from ..telemetry import temporal_interceptors

        interceptors = temporal_interceptors()
    kwargs: dict[str, Any] = {
        "namespace": settings.temporal_namespace,
        "data_converter": data_converter(objects),
        "interceptors": list(interceptors),
    }
    if settings.temporal_api_key:
        kwargs["api_key"] = settings.temporal_api_key
        kwargs["tls"] = True
    elif settings.temporal_tls:
        kwargs["tls"] = True
    return await Client.connect(settings.temporal_address, **kwargs)
