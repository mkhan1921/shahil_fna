"""The ``product`` grammar (§5.3): product and provider names from the catalogue, plus client-specific names.

Output: ``P;t=<product type>;[pv=<provider>;]k=<lexicon key>;``. It accepts a product ("retirement annuity",
"RA", "tax-free savings"), a provider ("Allan Gray"), a provider then a product ("Allan Gray RA",
"Discovery life cover"), or a product, a connector and a provider ("living annuity with Sanlam"). A
provider alone reads as product type ``institution``. Names passed per call (the client's own policies and
funds from the CRM or prior FnaDocument, §5.4) are compiled into a per-call grammar, cached by name set.
"""

from __future__ import annotations

import re
from functools import lru_cache

import pynini
from pynini.lib import pynutil

from .catalog import CONNECTORS, PRODUCTS, PROVIDERS, ProductName
from .tokens import normalize_text

_UNSAFE_OUT = re.compile(r"[;=\[\]\\]")


def _clean(s: str) -> str:
    return _UNSAFE_OUT.sub(" ", s).strip()


@lru_cache(maxsize=1)
def _catalogue_arms() -> tuple[list[pynini.Fst], list[pynini.Fst]]:
    products = [
        pynutil.add_weight(pynini.cross(surface, f"t={p.product_type};k={p.key};"), p.cost) for p in PRODUCTS for surface in p.surfaces
    ]
    providers = [pynini.cross(surface, f"pv={_clean(pv.name)};") for pv in PROVIDERS for surface in pv.surfaces]
    return products, providers


def _extra_arms(extra: tuple[ProductName, ...]) -> tuple[list[pynini.Fst], list[pynini.Fst]]:
    products, providers = [], []
    for name in extra:
        surface = normalize_text(name.surface)
        if not surface:
            continue
        if name.product_type:
            out = f"t={_clean(name.product_type)};" + (f"pv={_clean(name.provider)};" if name.provider else "") + "k=client;"
            products.append(pynini.cross(pynini.escape(surface), out))
        elif name.provider:
            providers.append(pynini.cross(pynini.escape(surface), f"pv={_clean(name.provider)};"))
    return products, providers


@lru_cache(maxsize=64)
def product_grammar(extra: tuple[ProductName, ...] = ()) -> pynini.Fst:
    """The compiled product grammar, extended with the call's own names (cached per name set)."""
    base_products, base_providers = _catalogue_arms()
    extra_products, extra_providers = _extra_arms(extra)
    products = pynini.union(*base_products, *extra_products)
    providers = pynini.union(*base_providers, *extra_providers)
    connector = pynini.union(*(pynutil.delete(f" {c} ") for c in CONNECTORS))
    body = pynini.union(
        providers + pynutil.delete(" ") + products,
        products + connector + providers,
        products,
        providers + pynutil.insert("t=institution;k=provider;"),
    )
    fst = (pynutil.insert("P;") + body).optimize()
    fst.arcsort("ilabel")
    return fst
