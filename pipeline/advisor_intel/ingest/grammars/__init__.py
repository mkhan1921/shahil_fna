"""Weighted Pynini grammars for spoken amounts, ages and product names (spec §5.3).

``GRAMMAR_VERSION`` is pinned per run (``VersionPins.grammar_version``): any change to a grammar, its
weights or the catalogue is a new version. The grammars compile once per process and are cached.

Typical use::

    from advisor_intel.ingest import grammars
    grammars.parse("two fifty", "amount").readings      # 250, 250,000 and 2,500,000
    grammars.lattice_readings([[("two", .9)], [("fifty", .6), ("fifteen", .35)]], "amount")
"""

from __future__ import annotations

from .age import age_grammar
from .amount import COSTS as AMOUNT_COSTS
from .amount import amount_grammar
from .catalog import FNA_LIST, PRODUCTS, PROVIDERS, ProductName, provider_for
from .lattice import (
    GrammarResult,
    accepts,
    build_lattice,
    lattice_readings,
    merge_readings,
    parse,
    reading_identity,
    readings_for_lattice,
    same_value,
)
from .product import product_grammar
from .tokens import normalize_text, normalize_token, tokens
from .verbalize import (
    age_spoken_forms,
    amount_spoken_forms,
    format_reading,
    int_to_words,
    number_to_words,
    reading_spoken_forms,
    written_amount,
)

GRAMMAR_VERSION = "2"  # 2: "twenty two fifty", "a million and a half", zero, literal cents, written decades ("60s")


def warm() -> None:
    """Compile every grammar now (workers call this at start-up so the first call is not slow)."""
    amount_grammar()
    age_grammar()
    product_grammar()


__all__ = [
    "AMOUNT_COSTS",
    "FNA_LIST",
    "GRAMMAR_VERSION",
    "PRODUCTS",
    "PROVIDERS",
    "GrammarResult",
    "ProductName",
    "accepts",
    "age_grammar",
    "age_spoken_forms",
    "amount_grammar",
    "amount_spoken_forms",
    "build_lattice",
    "format_reading",
    "int_to_words",
    "lattice_readings",
    "merge_readings",
    "normalize_text",
    "normalize_token",
    "number_to_words",
    "parse",
    "product_grammar",
    "provider_for",
    "reading_identity",
    "reading_spoken_forms",
    "readings_for_lattice",
    "same_value",
    "tokens",
    "warm",
    "written_amount",
]
