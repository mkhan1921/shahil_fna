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


def default_services(*, glossary_store: Any = None, web_search: Any = None, **overrides: Any) -> HarnessServices:
    """Services wired from every stage that is installed; ``overrides`` replace any of them.

    ``glossary_store`` is the practice glossary (a ``lexicon.glossary.GlossaryStore``) and
    ``web_search`` the allow-listed search callable for term lookup; without them lookup still
    searches the product catalogue and the client's CRM and prior-file terms.
    """
    services = HarnessServices(extract=_extract, derive=_derive)
    for name, fn in _wired(glossary_store=glossary_store, web_search=web_search).items():
        if fn is not None:
            setattr(services, name, fn)
    for k, v in overrides.items():
        setattr(services, k, v)
    return services


def _crm_terms(crm: Any) -> list[str]:
    if crm is None:
        return []
    terms = [p.name for p in crm.household] + [t for pr in crm.products for t in (pr.provider, pr.product)]
    return [t for t in dict.fromkeys(terms) if t]


def make_lookup_term(glossary_store: Any = None, *, web_search: Any = None, allowlist: list[str] | None = None) -> Any:
    """The ``lookup_term`` service (§8.5): practice glossary, catalogue and the client's known terms, then web search."""
    import asyncio

    from ..lexicon import glossary as G

    store = glossary_store if glossary_store is not None else G.InMemoryGlossary()

    async def lookup_term(term: str, context: str, deps: HarnessDeps) -> ServiceResult:
        inputs = deps.inputs
        kw: dict[str, Any] = {"text": context, "crm_terms": _crm_terms(inputs.crm), "practice_products": list(inputs.glossary)}
        ctx = G.TermContext.from_fna(inputs.base_document, **kw) if inputs.base_document is not None else G.TermContext(**kw)
        res = await asyncio.to_thread(G.lookup_term, term, ctx, glossary=store, web_search=web_search, allowlist=allowlist)
        return ServiceResult(data={**res.model_dump(mode="json"), "resolved": res.resolved})

    return lookup_term


def _wired(*, glossary_store: Any = None, web_search: Any = None) -> dict[str, Any]:
    """Verification, constraints, sensitivity, follow-ups and term lookup, when their modules exist."""
    out: dict[str, Any] = {}
    if _optional("advisor_intel.lexicon.glossary") is not None:
        out["lookup_term"] = make_lookup_term(glossary_store, web_search=web_search)
    return out
