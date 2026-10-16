"""Unknown terms and the practice glossary (spec §8.5).

Product names, provider brands, scheme names and slang not covered by the context pack are resolved
with tools, never guessed, and confirmed answers are remembered:

- **Detection** — ``is_unknown(term, context)``: the term matches nothing in the CRM record, the prior
  FnaDocument, the product catalogue, the glossary or the product grammar.
- **Lookup** — ``lookup_term(term, context, ...)`` searches, in order, the practice glossary; the
  product catalogue and the practice's provider and product lists; then web search limited to the
  allow-list of SA provider and regulator domains (``Settings.web_search_allowlist``). It returns
  candidates (canonical name, product type, provider, source URL) with scores.
- **Resolution** — one candidate scoring >= 0.8 (or one clearly ahead of the rest) is used and the
  fact records ``term_resolution = {term, candidate, source}``. Otherwise the term is ``unresolved``
  with its candidates, the product type falls back to ``other`` and a tier A fact raises a blocking
  flag (§10.3).
- **Memory** — the reviewer confirms or corrects a resolution in one click; ``confirm_term`` writes it
  to the glossary. Web-sourced answers enter the glossary only through that confirmation.

The glossary store is a protocol: an in-memory and a JSON-file store live here; the Postgres
``glossary`` table (§14.2) implements the same three methods.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel
from rapidfuzz import fuzz

from ..config import get_settings
from ..models.fna import FnaDocument
from ..models.profile import BlockingFlag
from .phrases import canon

log = logging.getLogger(__name__)

CATALOGUE_DIR = Path(__file__).parent
RESOLVE_AT = 0.8  # §8.5: one candidate scoring >= 0.8 resolves the term
CLEAR_MARGIN = 0.1  # several above 0.8: the top one resolves only when this far ahead
KNOWN_AT = 0.9  # is_unknown: a name this close to a known name is known
CANDIDATE_FLOOR = 0.5  # weaker matches are not reported as candidates
WEB_TRUST = 0.95  # web candidates score a little below the same match from a curated source
CONTEXT_PENALTY = 0.1  # a catalogue entry with context words, none of which appear around the term
# Words too common to identify a product: alone they match only exactly, never as part of a longer name,
# and when two names share them only the remaining, distinctive words are compared.
_GENERIC = {
    "plus", "max", "balanced", "growth", "income", "life", "fund", "funds", "plan", "cover", "policy", "investment", "investments", "savings",
    "account", "bank", "group", "equity", "money", "market", "flexible", "core", "classic", "essential", "opportunity", "protector", "provider",
    "wealth", "retirement", "pension", "annuity", "premium", "benefit", "select", "smart", "global", "local", "top", "insurance", "assurance",
    "asset", "assets", "management", "managers", "holdings", "capital", "partners", "financial", "services", "platform",
}  # fmt: skip
CatalogueKind = Literal["fund", "fund_component", "asset", "policy", "liability", "expense", "provider", "other"]
Source = Literal["glossary", "catalogue", "practice", "web", "reviewer"]
_FNA_LIST = {"fund": "retirementFunds", "fund_component": "retirementFunds", "asset": "assets", "policy": "policies", "liability": "liabilities"}
_FILLER = {"my", "the", "our", "his", "her", "their", "with", "from", "at", "by", "through", "via", "n", "a", "an", "one", "that", "this"}


# --------------------------------------------------------------------------- the product catalogue


class CatalogueEntry(BaseModel):
    name: str
    aliases: list[str] = []
    kind: CatalogueKind
    fna_type: str | None = None  # the FnaDocument union value ('retirement-annuity', 'tfsa', 'life', …)
    provider: str | None = None
    is_group: bool = False
    rule: str = ""  # one-line interpretation rule for the context pack (§7.2)
    context_words: list[str] = []  # words near the term that point to this entry ('provident' → provident preservation)
    kinds: list[str] = []  # providers: insurer, asset_manager, platform, bank …
    domain: str | None = None  # providers: the website, for web results

    @property
    def fna_list(self) -> str | None:
        return _FNA_LIST.get(self.kind)

    def names(self) -> list[str]:
        return [self.name, *self.aliases]


class ProductCatalogue:
    """FNA product types, the major SA providers and well-known branded products (§7.2, §8.5)."""

    def __init__(self, version: str, products: Sequence[CatalogueEntry], providers: Sequence[CatalogueEntry], named: Sequence[CatalogueEntry]) -> None:
        self.version = version
        self.products = list(products)
        self.providers = list(providers)
        self.named_products = list(named)

    @classmethod
    def from_yaml(cls, text: str) -> "ProductCatalogue":
        data = yaml.safe_load(text)
        prods = [CatalogueEntry.model_validate(p) for p in data.get("products", [])]
        provs = [CatalogueEntry.model_validate({**p, "kind": "provider"}) for p in data.get("providers", [])]
        named = [CatalogueEntry.model_validate(p) for p in data.get("named_products", [])]
        return cls(str(data["version"]), prods, provs, named)

    def entries(self) -> list[CatalogueEntry]:
        return [*self.products, *self.named_products, *self.providers]

    def provider_for_domain(self, host: str) -> CatalogueEntry | None:
        host = host.lower().removeprefix("www.")
        for p in self.providers:
            if p.domain and (host == p.domain or host.endswith("." + p.domain)):
                return p
        return None

    def context_lines(self, practice_providers: Iterable[str] = (), practice_products: Iterable[str] = ()) -> list[str]:
        """The catalogue part of the extraction context pack (§7.2): types with their rules, then provider names."""
        lines = [f"{p.name} ({', '.join(p.aliases[:3])}): {p.kind} → {p.fna_type or 'no FNA type'}. {p.rule}".strip() for p in self.products]
        names = sorted({p.name for p in self.providers} | set(practice_providers))
        lines.append("Providers: " + ", ".join(names))
        products = sorted({p.name for p in self.named_products} | set(practice_products))
        if products:
            lines.append("Products: " + ", ".join(products))
        return lines


@functools.lru_cache(maxsize=4)
def load_catalogue(version: str = "1") -> ProductCatalogue:
    return ProductCatalogue.from_yaml((CATALOGUE_DIR / f"catalogue_za_v{version}.yaml").read_text())


# --------------------------------------------------------------------------- the glossary store


class GlossaryEntry(BaseModel):
    """One adviser-confirmed term meaning (the ``glossary`` table, §14.2)."""

    term: str
    aliases: list[str] = []
    canonical_name: str
    product_type: str | None = None
    provider: str | None = None
    kind: str | None = None
    source: str = "reviewer"  # where the confirmed answer came from: glossary, catalogue, practice, web or reviewer
    source_url: str | None = None
    confirmed_by: str
    confirmed_at: datetime


@runtime_checkable
class GlossaryStore(Protocol):
    def get(self, term: str) -> GlossaryEntry | None: ...

    def entries(self) -> list[GlossaryEntry]: ...

    def put(self, entry: GlossaryEntry) -> None: ...


class InMemoryGlossary:
    """A glossary held in memory, keyed by canonical term (aliases resolve too)."""

    def __init__(self, entries: Iterable[GlossaryEntry] = ()) -> None:
        self._entries: dict[str, GlossaryEntry] = {}
        for e in entries:
            self._entries[canon(e.term)] = e

    def get(self, term: str) -> GlossaryEntry | None:
        c = canon(term)
        if c in self._entries:
            return self._entries[c]
        return next((e for e in self._entries.values() if c in {canon(a) for a in e.aliases}), None)

    def entries(self) -> list[GlossaryEntry]:
        return list(self._entries.values())

    def put(self, entry: GlossaryEntry) -> None:
        self._entries[canon(entry.term)] = entry


class JsonGlossary(InMemoryGlossary):
    """A glossary persisted to a JSON file (written atomically on every put)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        entries = []
        if self.path.exists():
            entries = [GlossaryEntry.model_validate(e) for e in json.loads(self.path.read_text() or "[]")]
        super().__init__(entries)

    def put(self, entry: GlossaryEntry) -> None:
        super().put(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([e.model_dump(mode="json") for e in self.entries()], indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
        os.replace(tmp, self.path)


# --------------------------------------------------------------------------- matching


def name_score(a: str, b: str) -> float:
    """Similarity of two names in [0, 1]: exact canonical match 1; typos via ratio; word subsets slightly less.

    A single generic word ('plus', 'balanced') never matches as a subset of a longer name.
    """
    ca, cb = canon(a), canon(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    ta, tb = ca.split(), cb.split()
    shared = set(ta) & set(tb) & _GENERIC
    ra, rb = " ".join(t for t in ta if t not in shared), " ".join(t for t in tb if t not in shared)
    if shared and ra and rb:  # 'Ace Investments' vs 'Absa Investments': only 'ace' vs 'absa' counts
        chars = max(fuzz.ratio(ra, rb), fuzz.token_sort_ratio(ra, rb))
    else:
        chars = max(fuzz.ratio(ca, cb), fuzz.token_sort_ratio(ca, cb))
    subset = 0.85 * fuzz.token_set_ratio(ca, cb)
    if any(len(t) == 1 and t[0] in _GENERIC for t in (ta, tb)):
        subset = 0.0
    return max(chars, subset) / 100.0


def _best(term: str, names: Iterable[str]) -> float:
    return max((name_score(term, n) for n in names), default=0.0)


@dataclass
class TermContext:
    """What is already known about the client and the practice when a term comes up (§8.5)."""

    text: str = ""  # the span or segment around the term, for disambiguation
    crm_terms: Sequence[str] = ()  # CRM product holdings, providers, people and businesses
    prior_fna_terms: Sequence[str] = ()  # providers, insurers, lenders and product names in the prior FnaDocument
    practice_providers: Sequence[str] = ()
    practice_products: Sequence[CatalogueEntry | str] = ()
    grammar_products: Sequence[str] = ()  # canonical names the product grammar accepts (§5.3)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_fna(cls, doc: FnaDocument, **kw: Any) -> "TermContext":
        """A context whose prior-FNA terms are the document's providers, insurers, lenders and product names."""
        terms: list[str] = []
        for f in doc.retirementFunds:
            terms += [f.provider, f.name]
        for a in doc.assets:
            terms += [a.provider, a.description]
        for li in doc.liabilities:
            terms += [li.lender, li.description]
        for p in doc.policies:
            terms += [p.insurer]
        kw.setdefault("prior_fna_terms", [t for t in dict.fromkeys(terms) if t])
        return cls(**kw)

    def practice_entries(self) -> list[CatalogueEntry]:
        out = [CatalogueEntry(name=p, kind="provider") for p in self.practice_providers]
        for p in self.practice_products:
            out.append(p if isinstance(p, CatalogueEntry) else CatalogueEntry(name=p, kind="other"))
        return out


def is_unknown(term: str, context: TermContext, *, glossary: GlossaryStore | None = None, catalogue: ProductCatalogue | None = None) -> bool:
    """True when the term matches nothing the pipeline already knows (§8.5 detection)."""
    if not canon(term):
        return False
    known: list[str] = [*context.crm_terms, *context.prior_fna_terms, *context.grammar_products]
    known += [n for e in context.practice_entries() for n in e.names()]
    known += [n for e in (catalogue or load_catalogue()).entries() for n in e.names()]
    if glossary is not None:
        known += [n for e in glossary.entries() for n in (e.term, e.canonical_name, *e.aliases)]
    return _best(term, known) < KNOWN_AT


# --------------------------------------------------------------------------- lookup


class TermCandidate(BaseModel):
    canonical_name: str
    product_type: str | None = None  # FNA union value; None when the term names only a provider
    provider: str | None = None
    kind: str | None = None
    source: Source
    source_url: str | None = None
    score: float


class TermLookup(BaseModel):
    term: str
    status: Literal["resolved", "unresolved"]
    candidate: TermCandidate | None = None
    candidates: list[TermCandidate] = []  # best first
    source: Source | None = None
    product_type: str | None = None  # the candidate's type when resolved; 'other' when unresolved (§8.5)
    searched: list[str] = []  # sources consulted, in order
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"

    def as_term_resolution(self) -> dict[str, Any]:
        """The ``Fact.term_resolution`` record: {term, candidate, source}, or the unresolved candidates."""
        if self.resolved and self.candidate is not None:
            return {"term": self.term, "candidate": self.candidate.model_dump(mode="json"), "source": self.source}
        return {"term": self.term, "status": "unresolved", "product_type": "other", "candidates": [c.model_dump(mode="json") for c in self.candidates],
                "reason": self.reason}  # fmt: skip

    def blocking_flag(self, fact_id: str, tier: str) -> BlockingFlag | None:
        """§10.3: a tier A fact that depends on an unresolved term blocks review submission."""
        if self.resolved or tier != "A":
            return None
        return BlockingFlag(flag_id=f"unresolved_term:{fact_id}:{canon(self.term)}", kind="unresolved_term", fact_id=fact_id,
                            message=f"'{self.term}' could not be resolved to a product or provider; confirm it or pick the product type")  # fmt: skip


class WebResult(BaseModel):
    url: str
    title: str
    snippet: str = ""


WebSearch = Callable[[str, Sequence[str]], Sequence[WebResult | Mapping[str, Any]]]


def _candidate(e: CatalogueEntry, score: float, source: Source, provider: str | None = None, name: str | None = None) -> TermCandidate:
    return TermCandidate(canonical_name=name or e.name, product_type=e.fna_type, provider=provider or e.provider or (e.name if e.kind == "provider" else None),
                         kind=e.kind, source=source, score=round(min(1.0, score), 4))  # fmt: skip


def _strip_filler(c: str) -> str:
    return " ".join(t for t in c.split() if t not in _FILLER)


def _catalogue_candidates(term: str, catalogue: ProductCatalogue, context: TermContext) -> list[TermCandidate]:
    c = _strip_filler(canon(term))
    out: list[TermCandidate] = []
    ctx_words = set(canon(context.text).split())
    for source, entries in (("catalogue", [*catalogue.products, *catalogue.named_products, *catalogue.providers]), ("practice", context.practice_entries())):
        for e in entries:
            s = _best(c, e.names())
            if e.context_words and not ctx_words & {canon(w) for w in e.context_words}:
                s -= CONTEXT_PENALTY
            if s >= CANDIDATE_FLOOR:
                out.append(_candidate(e, s, source))  # type: ignore[arg-type]
    # Provider + product: "my Allan Gray RA", "the Old Mutual funeral policy".
    providers = [*catalogue.providers, *[e for e in context.practice_entries() if e.kind == "provider"]]
    padded = f" {c} "
    for p in providers:
        for alias in p.names():
            ca = canon(alias)
            if not ca or f" {ca} " not in padded:
                continue
            rest = _strip_filler(padded.replace(f" {ca} ", " ", 1).strip())
            if not rest:
                continue
            for prod in [*catalogue.products, *catalogue.named_products]:
                s = _best(rest, prod.names())
                if s >= RESOLVE_AT:
                    out.append(_candidate(prod, s, "catalogue", provider=p.name, name=f"{p.name} {prod.name}"))
            break
    return out


def _web_candidates(term: str, web_search: WebSearch, allowlist: Sequence[str], catalogue: ProductCatalogue) -> list[TermCandidate]:
    try:
        results = web_search(term, list(allowlist))
    except Exception as e:  # a failing search leaves the term unresolved, never breaks extraction
        log.warning("web search for %r failed: %s", term, e)
        return []
    out: list[TermCandidate] = []
    for raw in results:
        r = raw if isinstance(raw, WebResult) else WebResult.model_validate(raw)
        host = (urlparse(r.url).hostname or "").lower()
        if not any(host == d or host.endswith("." + d) for d in allowlist):
            log.info("ignored web result outside the allow-list: %s", r.url)
            continue
        name = r.title.split("|")[0].split(" - ")[0].strip() or r.title
        score = name_score(term, name)
        if canon(term) and f" {canon(term)} " in f" {canon(r.title)} {canon(r.snippet)} ":
            score = max(score, 0.85)
        text = f" {canon(r.title)} {canon(r.snippet)} "
        typed = [(len(canon(n)), p) for p in catalogue.products for n in p.names() if canon(n) and f" {canon(n)} " in text]
        prod = max(typed, key=lambda t: t[0])[1] if typed else None
        prov = catalogue.provider_for_domain(host)
        if score * WEB_TRUST >= CANDIDATE_FLOOR:
            out.append(TermCandidate(canonical_name=name, product_type=prod.fna_type if prod else None, provider=prov.name if prov else None,
                                     kind=prod.kind if prod else None, source="web", source_url=r.url, score=round(score * WEB_TRUST, 4)))  # fmt: skip
    return out


def _dedupe(cands: Iterable[TermCandidate]) -> list[TermCandidate]:
    best: dict[tuple[str, str | None, str | None], TermCandidate] = {}
    for c in cands:
        k = (canon(c.canonical_name), c.product_type, c.provider)
        if k not in best or c.score > best[k].score:
            best[k] = c
    return sorted(best.values(), key=lambda c: -c.score)


def _decide(cands: list[TermCandidate]) -> tuple[TermCandidate | None, bool]:
    """(resolved candidate, ambiguous): one candidate >= 0.8, or one clearly ahead of the others above 0.8."""
    strong = [c for c in cands if c.score >= RESOLVE_AT]
    if len(strong) == 1:
        return strong[0], False
    if len(strong) > 1:
        same = {(c.product_type, c.provider) for c in strong}
        if len(same) == 1 or round(strong[0].score - strong[1].score, 6) >= CLEAR_MARGIN:
            return strong[0], False
        return None, True
    return None, False


def lookup_term(
    term: str,
    context: TermContext,
    *,
    glossary: GlossaryStore,
    catalogue: ProductCatalogue | None = None,
    web_search: WebSearch | None = None,
    allowlist: Sequence[str] | None = None,
) -> TermLookup:
    """Resolve an unknown product, provider or scheme name (§8.5): glossary, then catalogue and practice lists, then allow-listed web search."""
    catalogue = catalogue or load_catalogue()
    allowlist = list(allowlist) if allowlist is not None else list(get_settings().web_search_allowlist)
    searched: list[str] = []
    seen: list[TermCandidate] = []

    def done(cands: list[TermCandidate], source: Source) -> TermLookup | None:
        seen.extend(cands)
        chosen, ambiguous = _decide(cands)
        if chosen is not None:
            return TermLookup(term=term, status="resolved", candidate=chosen, candidates=_dedupe(seen)[:5], source=chosen.source,
                              product_type=chosen.product_type, searched=list(searched))  # fmt: skip
        if ambiguous:
            return TermLookup(term=term, status="unresolved", candidates=_dedupe(seen)[:5], product_type="other", searched=list(searched),
                              reason=f"several {source} candidates score >= {RESOLVE_AT} without a clear winner")  # fmt: skip
        return None

    searched.append("glossary")
    g = []
    for e in glossary.entries():
        s = _best(_strip_filler(canon(term)) or term, [e.term, e.canonical_name, *e.aliases])
        if s >= CANDIDATE_FLOOR:
            g.append(TermCandidate(canonical_name=e.canonical_name, product_type=e.product_type, provider=e.provider, kind=e.kind, source="glossary",
                                   source_url=e.source_url, score=round(s, 4)))  # fmt: skip
    if (res := done(_dedupe(g), "glossary")) is not None:
        return res
    searched.append("catalogue")
    if (res := done(_dedupe(_catalogue_candidates(term, catalogue, context)), "catalogue")) is not None:
        return res
    if web_search is not None and allowlist:
        searched.append("web")
        if (res := done(_dedupe(_web_candidates(term, web_search, allowlist, catalogue)), "web")) is not None:
            return res
    return TermLookup(term=term, status="unresolved", candidates=_dedupe(seen)[:5], product_type="other", searched=searched,
                      reason=f"no candidate scores >= {RESOLVE_AT}")  # fmt: skip


def confirm_term(
    store: GlossaryStore,
    term: str,
    *,
    canonical_name: str,
    confirmed_by: str,
    product_type: str | None = None,
    provider: str | None = None,
    kind: str | None = None,
    source: str = "reviewer",
    source_url: str | None = None,
    aliases: Iterable[str] = (),
    at: datetime | None = None,
) -> GlossaryEntry:
    """Write a reviewer-confirmed meaning to the glossary (§8.5 memory, §11.2 'Confirm term')."""
    if not confirmed_by:
        raise ValueError("a glossary entry needs the adviser who confirmed it")
    existing = store.get(term)
    entry = GlossaryEntry(
        term=term,
        aliases=sorted({*(existing.aliases if existing else []), *aliases} - {term}),
        canonical_name=canonical_name,
        product_type=product_type,
        provider=provider,
        kind=kind,
        source=source,
        source_url=source_url,
        confirmed_by=confirmed_by,
        confirmed_at=at or datetime.now(timezone.utc),
    )
    store.put(entry)
    return entry


def confirm_lookup(store: GlossaryStore, lookup: TermLookup, *, confirmed_by: str, correction: TermCandidate | None = None,
                   at: datetime | None = None) -> GlossaryEntry:  # fmt: skip
    """One-click confirmation of a lookup's resolution, or of the reviewer's correction."""
    chosen = correction or lookup.candidate
    if chosen is None:
        raise ValueError(f"{lookup.term!r} is unresolved: pass the reviewer's correction")
    return confirm_term(store, lookup.term, canonical_name=chosen.canonical_name, confirmed_by=confirmed_by, product_type=chosen.product_type,
                        provider=chosen.provider, kind=chosen.kind, source="reviewer" if correction else chosen.source,
                        source_url=chosen.source_url, at=at)  # fmt: skip


__all__ = [
    "CatalogueEntry",
    "GlossaryEntry",
    "GlossaryStore",
    "InMemoryGlossary",
    "JsonGlossary",
    "ProductCatalogue",
    "TermCandidate",
    "TermContext",
    "TermLookup",
    "WebResult",
    "WebSearch",
    "confirm_lookup",
    "confirm_term",
    "is_unknown",
    "load_catalogue",
    "lookup_term",
    "name_score",
]
