"""Connecting to Temporal: Temporal Cloud with an API key and TLS, or a local dev server (spec §1.3, §13.1).

Clients and workers share one data converter (Pydantic payloads plus payload offload to object
storage) and the OpenTelemetry interceptor, so traces continue from the API into the workflow.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from temporalio.client import Client

from ..config import Settings
from ..storage.objects import ObjectStore
from .converter import data_converter


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
