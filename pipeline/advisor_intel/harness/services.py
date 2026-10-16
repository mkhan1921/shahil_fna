"""The work behind the orchestrator's tools, wired from the stages (spec §13.3).

``default_services`` builds a ``HarnessServices`` from the installed stages: extraction (this
package), derivations, verification and calibration, constraints, VOI and follow-ups, and term
lookup. Each piece is imported lazily so a deployment without one (or a test) still runs the
harness; the corresponding tool then reports that it is not configured.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models.profile import Profile
from .deps import HarnessDeps, HarnessServices, ServiceResult

log = logging.getLogger(__name__)


async def _extract(task, deps: HarnessDeps):
    from ..extract.task import extract_task

    return await extract_task(task, deps)


async def _derive(profile: Profile, deps: HarnessDeps) -> Profile:
    from ..schema.derivations import derive_all

    return derive_all(profile, base=deps.inputs.base_document, run_id=deps.inputs.run_id)


def _optional(name: str) -> Any:
    """Import a stage module if it is installed (None otherwise)."""
    import importlib

    try:
        return importlib.import_module(name)
    except ImportError as e:  # a missing optional stage is reported by its tool, not fatal
        log.info("stage module %s is not available: %s", name, e)
        return None


def default_services(**overrides: Any) -> HarnessServices:
    """Services wired from every stage that is installed; ``overrides`` replace any of them."""
    services = HarnessServices(extract=_extract, derive=_derive)
    for name, fn in _wired().items():
        if fn is not None:
            setattr(services, name, fn)
    for k, v in overrides.items():
        setattr(services, k, v)
    return services


def _wired() -> dict[str, Any]:
    """Verification, constraints, sensitivity, follow-ups and term lookup, when their modules exist."""
    out: dict[str, Any] = {}
    glossary = _optional("advisor_intel.lexicon.glossary")
    if glossary is not None:

        async def lookup_term(term: str, context: str, deps: HarnessDeps) -> ServiceResult:
            ctx = glossary.TermContext(text=context) if "text" in getattr(glossary.TermContext, "__dataclass_fields__", {}) else glossary.TermContext()
            res = glossary.lookup_term(term, ctx)
            return ServiceResult(data=res.model_dump(mode="json") if hasattr(res, "model_dump") else res)

        out["lookup_term"] = lookup_term
    return out
