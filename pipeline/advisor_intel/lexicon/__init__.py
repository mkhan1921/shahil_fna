"""Normalisation and the vagueness lexicon (spec §8).

The LLM never invents numbers for vague language: it returns a typed intermediate form (§8.1) and
deterministic code here turns that form into a distribution using a versioned lexicon (§8.2–8.3).

- ``load_lexicon(version)`` — the pinned lexicon and its lookups (``loader``);
- ``normalize_value(form, slot, profile, lex, ...)`` — §8.3 (``normalize``);
- ``lookup_hedges`` / ``hedge_strength_max`` — §7.3 step 5 (``hedges``);
- ``check_bounds`` / ``filter_bounds`` — §7.3 step 6 (``bounds``);
- ``assign_weights`` / ``build_hypotheses`` — §7.3 step 7 (``weighting``);
- ``propose_lexicon_fit`` — §11.4 item 2 (``fit``); golden cases — §8.4 (``golden``);
- ``is_unknown`` / ``lookup_term`` and the practice glossary — §8.5 (``glossary``).

Scale resolution for spoken amounts (§7.4) lives in ``advisor_intel.extract.scale``.
"""

from .bounds import BoundsCheck, BoundsResult, apply_bounds, check_bounds, filter_bounds
from .fit import EditPair, LexiconDiff, propose_lexicon_fit
from .glossary import (
    GlossaryEntry,
    GlossaryStore,
    InMemoryGlossary,
    JsonGlossary,
    ProductCatalogue,
    TermCandidate,
    TermContext,
    TermLookup,
    WebResult,
    confirm_lookup,
    confirm_term,
    is_unknown,
    load_catalogue,
    lookup_term,
)
from .hedges import HedgeResult, find_hedges, hedge_strength_max, lookup_hedges
from .loader import CURRENT_VERSION, Lexicon, Likelihood, Lookup, MissLog, MissRecord, RecencyWeights, load_lexicon
from .normalize import Normalized, normalize_value, quantity_unit, resolve_reference, todays_money_rate
from .weighting import assign_weights, build_hypotheses

__all__ = [
    "CURRENT_VERSION",
    "BoundsCheck",
    "BoundsResult",
    "EditPair",
    "GlossaryEntry",
    "GlossaryStore",
    "HedgeResult",
    "InMemoryGlossary",
    "JsonGlossary",
    "Lexicon",
    "LexiconDiff",
    "Likelihood",
    "Lookup",
    "MissLog",
    "MissRecord",
    "Normalized",
    "ProductCatalogue",
    "RecencyWeights",
    "TermCandidate",
    "TermContext",
    "TermLookup",
    "WebResult",
    "apply_bounds",
    "assign_weights",
    "build_hypotheses",
    "check_bounds",
    "confirm_lookup",
    "confirm_term",
    "filter_bounds",
    "find_hedges",
    "hedge_strength_max",
    "is_unknown",
    "load_catalogue",
    "load_lexicon",
    "lookup_hedges",
    "lookup_term",
    "normalize_value",
    "propose_lexicon_fit",
    "quantity_unit",
    "resolve_reference",
    "todays_money_rate",
]
