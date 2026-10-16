"""The versioned vagueness lexicon (spec §8.2) and its lookups (§8.3).

``load_lexicon(version)`` reads ``lexicon/v{version}.yaml`` once per version, validates it and
returns an immutable ``Lexicon``. Every lookup canonicalises the phrase (case, punctuation,
whitespace, hyphens, "60s" → "sixties", leading fillers and articles) and returns a ``Lookup`` that
says how it was resolved:

- ``exact``: a lexicon key (or a template such as "about {n}", or a number, or a composite
  "<vague count> × <scale word>" read from keys);
- ``near_miss``: no key matched, but the nearest key of the section has embedding similarity
  >= 0.8 (``params.near_miss_threshold``); it supplies a provisional prior, flag ``lexicon_near_miss``;
- ``miss``: nothing close; flag ``lexicon_miss``. Lookups that supply the whole value (vague
  counts, amounts, age bands, relative, rate and likelihood words) return no distribution and the
  caller falls back to the slot's population prior. Lookups that only qualify a stated value
  (approx words, hedges, certainty words) return the lexicon's default instead;
- ``default``: no word was given, so the lexicon default applies (not a miss).

Near-misses and misses are appended to ``Lexicon.miss_log`` with the phrase, for the weekly
lexicon review (§8.3). The embedder is injectable (``load_lexicon(..., embedder=...)``) and the
key vectors are embedded once per lexicon version and embedder (§8.3).
"""

from __future__ import annotations

import functools
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping, Sequence

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .. import stats
from ..embeddings import Embedder, cosine, get_embedder
from ..models.dists import Bernoulli, Discrete, Dist, LogNormal, Normal, Point, Triangular
from ..models.facts import Hedge
from .phrases import canon, parse_number, split_number, strip_numbers

LEXICON_DIR = Path(__file__).parent
CURRENT_VERSION = "1.3"

MatchKind = Literal["exact", "near_miss", "miss", "default"]
LexiconFlag = Literal["lexicon_near_miss", "lexicon_miss"]

SECTIONS = (
    "approx_words",
    "vague_counts",
    "vague_amounts",
    "scale_words",
    "age_bands",
    "age_offsets",
    "relative_words",
    "rate_words",
    "rate_templates",
    "likelihood_words",
    "hedge_strength",
    "certainty_words",
)
_ARTICLES = ("a ", "an ", "the ", "n ")
_FILLERS = {"so", "like", "um", "uh", "er", "erm", "well", "ja", "nee", "basically", "you know"}
_AGE_LEAD = {
    "in", "my", "his", "her", "their", "our", "your", "the", "at", "age", "aged", "of", "when", "i", "im", "hes", "shes",
    "he", "she", "is", "am", "was", "will", "be", "ill", "retire", "retiring", "stop", "working", "sy", "haar", "ons", "op",
    "ouderdom", "wanneer", "ek",
}  # fmt: skip
_AGE_TRAIL = {"years", "year", "old", "yrs", "jaar", "oud"}
_AGE_AF = {"vroee": "early", "vroeer": "early", "middel": "mid", "laat": "late", "middle": "mid"}
_CURRENCY_WORDS = {"rand", "randte", "r", "zar", "bucks", "dollars", "dollar", "usd", "pounds", "pound", "gbp", "euros", "euro", "eur"}


# --------------------------------------------------------------------------- the file format (validated)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ApproxEntry(_Frozen):
    """A spread around a stated value: ``rel_halfwidth`` or relative (low, mode, high) offsets."""

    dist: Literal["point", "triangular"]
    rel_halfwidth: float | None = None
    rel_low: float | None = None
    rel_mode: float | None = None
    rel_high: float | None = None

    @model_validator(mode="after")
    def _shape(self) -> "ApproxEntry":
        if self.dist == "point":
            return self
        sym = self.rel_halfwidth is not None
        asym = None not in (self.rel_low, self.rel_mode, self.rel_high)
        if sym == asym:
            raise ValueError("a triangular approx entry takes rel_halfwidth, or rel_low, rel_mode and rel_high")
        if sym and self.rel_halfwidth < 0:  # type: ignore[operator]
            raise ValueError("rel_halfwidth must be >= 0")
        if asym and not (self.rel_low <= self.rel_mode <= self.rel_high):  # type: ignore[operator]
            raise ValueError("approx offsets need rel_low <= rel_mode <= rel_high")
        return self

    def offsets(self) -> tuple[float, float, float]:
        """Relative (low, mode, high) offsets; zeros for a point."""
        if self.dist == "point":
            return (0.0, 0.0, 0.0)
        if self.rel_halfwidth is not None:
            return (-self.rel_halfwidth, 0.0, self.rel_halfwidth)
        return (self.rel_low, self.rel_mode, self.rel_high)  # type: ignore[return-value]

    def around(self, value: float) -> Dist:
        lo, mo, hi = self.offsets()
        if (lo, hi) == (0.0, 0.0) or value == 0:
            return Point(value=float(value))
        a = abs(value)
        return Triangular(low=value + a * lo, mode=value + a * mo, high=value + a * hi)


class DistEntry(_Frozen):
    """An absolute distribution (or, in ``age_offsets``, offsets from a stated age)."""

    dist: Literal["point", "triangular", "discrete", "normal", "lognormal"]
    value: float | None = None
    low: float | None = None
    mode: float | None = None
    high: float | None = None
    support: list[float] | None = None
    weights: list[float] | None = None
    mean: float | None = None
    sd: float | None = None
    median: float | None = None
    p10: float | None = None
    p90: float | None = None

    @model_validator(mode="after")
    def _valid(self) -> "DistEntry":
        self.to_dist()
        return self

    def to_dist(self, offset: float = 0.0) -> Dist:
        try:
            if self.dist == "point":
                return Point(value=float((self.value or 0.0) + offset))
            if self.dist == "triangular":
                return Triangular(low=self.low + offset, mode=self.mode + offset, high=self.high + offset)  # type: ignore[operator]
            if self.dist == "discrete":
                return Discrete(support=[s + offset for s in self.support], weights=list(self.weights))  # type: ignore[union-attr,arg-type]
            if self.dist == "normal":
                return Normal(mean=self.mean + offset, sd=self.sd)  # type: ignore[operator,arg-type]
            return LogNormal(median=self.median + offset, p10=self.p10 + offset, p90=self.p90 + offset)  # type: ignore[operator]
        except TypeError as e:
            raise ValueError(f"{self.dist} entry is missing a parameter") from e


class LikelihoodEntry(_Frozen):
    mean: float
    low: float
    high: float

    @model_validator(mode="after")
    def _ordered(self) -> "LikelihoodEntry":
        if not (0.0 <= self.low <= self.mean <= self.high <= 1.0):
            raise ValueError("likelihood needs 0 <= low <= mean <= high <= 1")
        return self


class RateTemplateEntry(_Frozen):
    sd: float


class RecencyWeights(_Frozen):
    revising: float
    revised: float


class CertaintyDefault(_Frozen):
    boolean: float
    category: float
    unknown_word: float


class Params(_Frozen):
    near_miss_threshold: float = 0.8
    range_widen_rel: float = 0.05
    age_range_widen_years: float = 0.5
    hedge_default_strength: float = 0.5
    approx_default: ApproxEntry
    likelihood_default: LikelihoodEntry
    certainty_default: CertaintyDefault


class Polarity(_Frozen):
    down: list[str]
    up: list[str]
    negation: list[str]


class LexiconFile(_Frozen):
    version: str
    params: Params
    approx_words: dict[str, ApproxEntry]
    vague_counts: dict[str, DistEntry]
    vague_amounts: dict[str, DistEntry]
    scale_words: dict[str, float]
    age_bands: dict[str, DistEntry]
    age_offsets: dict[str, DistEntry]
    relative_words: dict[str, DistEntry]
    rate_words: dict[str, DistEntry]
    rate_templates: dict[str, RateTemplateEntry]
    likelihood_words: dict[str, LikelihoodEntry]
    hedge_strength: dict[str, float]
    certainty_words: dict[str, float]
    category_synonyms: dict[str, str | list[str]]
    ordered_scales: list[list[str]]
    polarity: Polarity
    recency_weight: RecencyWeights

    @field_validator("version", mode="before")
    @classmethod
    def _version_str(cls, v: Any) -> str:
        return str(v)

    @model_validator(mode="after")
    def _ranges(self) -> "LexiconFile":
        for name in ("hedge_strength", "certainty_words"):
            bad = [k for k, v in getattr(self, name).items() if not 0.0 <= v <= 1.0]
            if bad:
                raise ValueError(f"{name} values must be in [0, 1]: {bad}")
        if any(v <= 0 for v in self.scale_words.values()):
            raise ValueError("scale words must be positive")
        if abs(self.recency_weight.revising + self.recency_weight.revised - 1.0) > 1e-9:
            raise ValueError("recency weights must sum to 1")
        return self


# --------------------------------------------------------------------------- lookup results


class Likelihood(_Frozen):
    """P(event) for review display (a Beta's mean and p10/p90); sampled as Bernoulli(mean) (§8.2)."""

    mean: float
    low: float
    high: float


class Lookup(BaseModel):
    """One lexicon lookup and how it was resolved."""

    model_config = ConfigDict(frozen=True)

    section: str
    phrase: str
    key: str | None = None  # the lexicon key used (template keys keep their "{n}")
    match: MatchKind
    similarity: float = 0.0  # 1 for exact hits; cosine for near-misses; best cosine on a miss
    dist: Dist | None = None
    value: float | None = None  # scale multiplier, hedge strength or certainty
    likelihood: Likelihood | None = None
    multiplier: float = 1.0  # composite vague amounts: the scale words' product
    base_value: float | None = None  # the stated number an approx word or template was applied to
    detail: str = ""

    @property
    def hit(self) -> bool:
        return self.match in ("exact", "near_miss", "default")

    @property
    def flag(self) -> LexiconFlag | None:
        return {"near_miss": "lexicon_near_miss", "miss": "lexicon_miss"}.get(self.match)  # type: ignore[return-value]

    def record(self) -> dict[str, Any]:
        """A compact, JSON-safe record for ``Fact.normalization`` (§8.3)."""
        out: dict[str, Any] = {"section": self.section, "phrase": self.phrase, "key": self.key, "match": self.match}
        if self.match == "near_miss" or (self.match == "miss" and self.similarity > 0):
            out["similarity"] = round(self.similarity, 4)
        if self.multiplier != 1.0:
            out["multiplier"] = self.multiplier
        if self.base_value is not None:
            out["base_value"] = self.base_value
        if self.detail:
            out["detail"] = self.detail
        return out


class MissRecord(BaseModel):
    section: str
    phrase: str
    canonical: str
    match: Literal["near_miss", "miss"]
    nearest_key: str | None
    similarity: float
    at: datetime


class MissLog:
    """Lexicon misses and near-misses with their phrases, for the weekly lexicon review (§8.3)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[MissRecord] = []

    def record(self, lookup: Lookup, canonical: str, nearest_key: str | None) -> None:
        if lookup.match not in ("near_miss", "miss"):
            return
        rec = MissRecord(
            section=lookup.section,
            phrase=lookup.phrase,
            canonical=canonical,
            match=lookup.match,
            nearest_key=nearest_key,
            similarity=round(lookup.similarity, 4),
            at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._items.append(rec)

    def entries(self) -> list[MissRecord]:
        with self._lock:
            return list(self._items)

    def drain(self) -> list[MissRecord]:
        """Return and clear the log (e.g. when a run persists it)."""
        with self._lock:
            items, self._items = self._items, []
            return items

    def counts(self) -> dict[tuple[str, str], int]:
        """(section, canonical phrase) → times seen, most frequent first: the weekly review list."""
        out: dict[tuple[str, str], int] = {}
        for r in self.entries():
            out[(r.section, r.canonical)] = out.get((r.section, r.canonical), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


# --------------------------------------------------------------------------- sections


def _template_regex(key: str) -> re.Pattern[str]:
    placeholder = "qqnqq"
    c = canon(key.replace("{n}", placeholder))
    return re.compile("^" + re.escape(c).replace(placeholder, r"(?P<n>.+?)") + "$")


class _Section:
    def __init__(self, name: str, entries: Mapping[str, Any]) -> None:
        self.name = name
        self.entries: Mapping[str, Any] = MappingProxyType(dict(entries))
        self.by_canon: dict[str, str] = {}
        self.templates: list[tuple[str, re.Pattern[str]]] = []
        for key in entries:
            if "{n}" in key:
                self.templates.append((key, _template_regex(key)))
                continue
            c = canon(key)
            if not c:
                raise ValueError(f"{name}: empty key {key!r}")
            if c in self.by_canon:
                raise ValueError(f"{name}: keys {self.by_canon[c]!r} and {key!r} are the same after canonicalisation")
            self.by_canon[c] = key
        self.literal_keys = list(self.by_canon.values())
        self.literal_canon = list(self.by_canon)

    def exact(self, c: str) -> str | None:
        return self.by_canon.get(c)

    def template(self, c: str) -> tuple[str, float] | None:
        for key, rx in self.templates:
            m = rx.match(c)
            if m:
                n = parse_number(m.group("n"))
                if n is not None:
                    return key, n
        return None


def variants(c: str) -> list[str]:
    """Simple variants of a canonical phrase, most literal first: fillers, articles, a trailing 'of'."""
    out = [c]
    toks = c.split()
    while toks and toks[0] in _FILLERS:
        toks = toks[1:]
        out.append(" ".join(toks))
    for v in list(out):
        if v.endswith(" of"):
            out.append(v[:-3])
    for v in list(out):
        stripped = next((v[len(a) :] for a in _ARTICLES if v.startswith(a)), None)
        out.append(stripped if stripped is not None else "a " + v)
    seen: set[str] = set()
    return [v for v in out if v and not (v in seen or seen.add(v))]


@dataclass(frozen=True)
class _Near:
    key: str | None
    similarity: float


# --------------------------------------------------------------------------- the lexicon


class Lexicon:
    """An immutable, versioned lexicon. Build it with ``load_lexicon`` (or ``Lexicon.from_text``)."""

    def __init__(self, data: LexiconFile, *, source_text: str, path: Path | None = None, embedder: Embedder | None = None,
                 miss_log: MissLog | None = None) -> None:  # fmt: skip
        self.data = data
        self.version = data.version
        self.params = data.params
        self.source_text = source_text
        self.path = path
        self.miss_log = miss_log if miss_log is not None else MissLog()
        self._embedder = embedder
        self._vecs: dict[tuple[str, str], np.ndarray] = {}
        self._vec_lock = threading.Lock()
        self.sections: Mapping[str, _Section] = MappingProxyType({name: _Section(name, getattr(data, name)) for name in SECTIONS})
        self._synonyms = {canon(k): ([v] if isinstance(v, str) else list(v)) for k, v in data.category_synonyms.items()}
        self._scales = [list(s) for s in data.ordered_scales]
        self._down = set(data.polarity.down)
        self._up = set(data.polarity.up)
        self._neg = set(data.polarity.negation)
        about = data.approx_words.get("about")
        about_age = data.age_offsets.get("about {n}")
        # Years of age per unit of relative half-width, so "ballpark sixty" is as wide in years as
        # "ballpark" is relative to "about" (± 2 years for "about sixty").
        self._age_years_per_rel = (about_age.high / about.rel_halfwidth) if about and about.rel_halfwidth and about_age and about_age.high else 20.0

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_text(cls, text: str, *, path: Path | None = None, embedder: Embedder | None = None) -> "Lexicon":
        data = LexiconFile.model_validate(yaml.safe_load(text))
        return cls(data, source_text=text, path=path, embedder=embedder)

    @classmethod
    def from_path(cls, path: Path, *, embedder: Embedder | None = None) -> "Lexicon":
        return cls.from_text(Path(path).read_text(), path=Path(path), embedder=embedder)

    def with_embedder(self, embedder: Embedder) -> "Lexicon":
        """The same lexicon bound to another embedder (own key-vector cache, shared miss log)."""
        return Lexicon(self.data, source_text=self.source_text, path=self.path, embedder=embedder, miss_log=self.miss_log)

    @property
    def embedder(self) -> Embedder:
        return self._embedder or get_embedder()

    def keys(self, section: str) -> list[str]:
        return list(self.sections[section].entries)

    def entry(self, section: str, key: str) -> Any:
        return self.sections[section].entries[key]

    # ------------------------------------------------------------------ near-miss machinery

    def _key_vectors(self, section: str) -> np.ndarray:
        emb = self.embedder
        k = (section, emb.name)
        with self._vec_lock:
            if k not in self._vecs:
                self._vecs[k] = emb.embed_documents(self.sections[section].literal_canon)
            return self._vecs[k]

    def _polarity(self, c: str) -> int:
        toks = set(c.split())
        down, up = bool(toks & self._down), bool(toks & self._up)
        return -1 if down and not up else (1 if up and not down else 0)

    def _negated(self, c: str) -> bool:
        return bool(set(c.split()) & self._neg)

    def _near(self, section: str, c: str) -> _Near:
        """The nearest literal key of a section (§8.3): best cosine, skipping keys of opposite polarity or negation."""
        sec = self.sections[section]
        if not c or not sec.literal_canon:
            return _Near(None, 0.0)
        sims = cosine(self.embedder.embed_queries([c]), self._key_vectors(section))[0]
        pol, neg = self._polarity(c), self._negated(c)
        for i in np.argsort(-sims):
            kc = sec.literal_canon[int(i)]
            kp = self._polarity(kc)
            if pol and kp and kp != pol:
                continue
            if neg != self._negated(kc):
                continue
            return _Near(sec.literal_keys[int(i)], float(sims[int(i)]))
        return _Near(None, 0.0)

    def _finish(self, lookup: Lookup, c: str, nearest: str | None = None) -> Lookup:
        self.miss_log.record(lookup, c, nearest)
        return lookup

    def _exact(self, section: str, c: str) -> str | None:
        sec = self.sections[section]
        for v in variants(c):
            k = sec.exact(v)
            if k:
                return k
        return None

    def _near_or_miss(self, section: str, phrase: str, c: str, make, *, miss_value: dict[str, Any] | None = None) -> Lookup:
        """Shared tail of every lookup: a near-miss supplies the nearest key's entry, else a miss."""
        near = self._near(section, c)
        if near.key is not None and near.similarity >= self.params.near_miss_threshold:
            fields = make(near.key)
            return self._finish(Lookup(section=section, phrase=phrase, key=near.key, match="near_miss", similarity=near.similarity, **fields), c, near.key)
        lk = Lookup(section=section, phrase=phrase, key=None, match="miss", similarity=near.similarity, **(miss_value or {}))
        return self._finish(lk, c, near.key)

    # ------------------------------------------------------------------ approx words

    def _approx_entry(self, word_c: str) -> str | None:
        return self._exact("approx_words", word_c)

    def _scale_tokens(self) -> set[str]:
        return {canon(k) for k in self.data.scale_words if len(canon(k).split()) == 1}

    def approx(self, word: str, value: float) -> Lookup:
        """A spread around a stated value (§8.3 AMOUNT_APPROX): 'about' 2.5m → Triangular(2.25m, 2.5m, 2.75m)."""
        c = canon(word)
        key = self._approx_entry(c) if c else None
        if key is None:  # the model may have quoted the number with the word: 'about 2.5 million'
            c = strip_numbers(c, extra=self._scale_tokens())
            if not c:
                return Lookup(section="approx_words", phrase=word, match="default", dist=self.params.approx_default.around(value), base_value=value,
                              detail="no approx word: params.approx_default")  # fmt: skip
            key = self._approx_entry(c)
        if key:
            return Lookup(section="approx_words", phrase=word, key=key, match="exact", similarity=1.0, dist=self.data.approx_words[key].around(value),
                          base_value=value)  # fmt: skip
        return self._near_or_miss(
            "approx_words", word, c,
            lambda k: {"dist": self.data.approx_words[k].around(value), "base_value": value},
            miss_value={"dist": self.params.approx_default.around(value), "base_value": value, "detail": "approx word not in the lexicon: params.approx_default"},
        )  # fmt: skip

    def _approx_number(self, c: str) -> tuple[str, float, Dist] | None:
        """'about five', 'five or so', 'sixty ish' → (approx key, number, spread)."""
        parts = split_number(c)
        if parts is None:
            return None
        before, n, after = parts
        if before and after:
            return None
        word = before or after
        if not word:
            return None
        key = self._approx_entry(word)
        if key is None:
            return None
        return key, n, self.data.approx_words[key].around(n)

    # ------------------------------------------------------------------ counts and amounts

    def _count_head(self, toks: list[str]) -> tuple[str, str | None, Dist, float | None, str] | None:
        """(section, key, dist, base number, detail) for the head of a composite amount, or None."""
        c = " ".join(toks)
        if c in ("", "a", "an", "one", "n", "een"):
            return ("scale_words", None, Point(value=1.0), None, "one")
        key = self._exact("vague_counts", c)
        if key:
            return ("vague_counts", key, self.data.vague_counts[key].to_dist(), None, "")
        n = parse_number(c)
        if n is not None:
            return ("scale_words", None, Point(value=n), n, "number")
        ap = self._approx_number(c)
        if ap:
            return ("approx_words", ap[0], ap[2], ap[1], "")
        return None

    def _scale_tail(self, toks: list[str]) -> tuple[float, list[str]] | None:
        """Read tokens as a run of scale words ('hundred k', 'half a bar'), longest words first."""
        if not toks:
            return 1.0, []
        sec = self.sections["scale_words"]
        for size in range(min(5, len(toks)), 0, -1):
            k = sec.exact(" ".join(toks[:size]))
            if k:
                rest = self._scale_tail(toks[size:])
                if rest is not None:
                    return self.data.scale_words[k] * rest[0], [k, *rest[1]]
        return None

    def _compose(self, phrase: str, c: str, *, need_scale: bool) -> Lookup | None:
        """'<vague count> <scale words>': 'a few hundred K' = 'a few hundred' × 1000; 'half a bar' = 500.

        The longest head that reads as a count wins, so a lexicon key such as 'a few hundred' is used
        (and re-fitted) as itself rather than as 'a few' × 'hundred'.
        """
        toks = [t for t in c.split() if t not in _CURRENCY_WORDS]
        for i in range(len(toks), -1, -1):
            head_toks, tail = toks[:i], toks[i:]
            while head_toks and head_toks[-1] in ("of", "van") and tail:
                head_toks = head_toks[:-1]
            if need_scale and not tail:
                continue
            scale = self._scale_tail(tail)
            if scale is None:
                continue
            head = self._count_head(head_toks)
            if head is None:
                continue
            mult, used = scale
            section, key, dist, base, detail = head
            if section == "scale_words" and used:
                key = used[-1]
            desc = " × ".join([" ".join(head_toks) or "one", *used]) if used else " ".join(head_toks)
            return Lookup(section=section, phrase=phrase, key=key, match="exact", similarity=1.0, dist=stats.scaled(dist, mult) if mult != 1 else dist,
                          multiplier=mult, base_value=base, detail=desc + (f" ({detail})" if detail else ""))  # fmt: skip
        return None

    def vague_count(self, word: str) -> Lookup:
        """A count multiplier: 'a few' → Triangular(2, 3, 5); 'about five' → 5 ± 10%; 'a couple of hundred' → 'a couple' × 100."""
        c = canon(word)
        key = self._exact("vague_counts", c)
        if key:
            return Lookup(section="vague_counts", phrase=word, key=key, match="exact", similarity=1.0, dist=self.data.vague_counts[key].to_dist())
        n = parse_number(c)
        if n is not None:
            return Lookup(section="vague_counts", phrase=word, match="exact", similarity=1.0, dist=Point(value=n), base_value=n, detail="number")
        ap = self._approx_number(c)
        if ap:
            return Lookup(section="approx_words", phrase=word, key=ap[0], match="exact", similarity=1.0, dist=ap[2], base_value=ap[1])
        comp = self._compose(word, c, need_scale=True)
        if comp:
            return comp
        return self._near_or_miss("vague_counts", word, c, lambda k: {"dist": self.data.vague_counts[k].to_dist()})

    def vague_amount(self, word: str) -> Lookup:
        """A vague amount in currency units (§8.3 AMOUNT_VAGUE): a whole-phrase key, or count × scale."""
        c = canon(word)
        key = self._exact("vague_amounts", c)
        if key:
            return Lookup(section="vague_amounts", phrase=word, key=key, match="exact", similarity=1.0, dist=self.data.vague_amounts[key].to_dist())
        comp = self._compose(word, c, need_scale=False)
        if comp:
            return comp
        return self._near_or_miss("vague_amounts", word, c, lambda k: {"dist": self.data.vague_amounts[k].to_dist()})

    def scale(self, word: str) -> Lookup:
        """The multiplier of a scale word: 'bar' → 1000, 'mil' → 1e6, 'half a bar' → 500."""
        c = canon(word)
        key = self._exact("scale_words", c)
        if key is None and parse_number(c) is None:  # 'two bar' → 'bar'
            c = strip_numbers(c) or c
            key = self._exact("scale_words", c)
        if key:
            v = self.data.scale_words[key]
            return Lookup(section="scale_words", phrase=word, key=key, match="exact", similarity=1.0, value=v, dist=Point(value=v))
        return self._near_or_miss("scale_words", word, c, lambda k: {"value": self.data.scale_words[k], "dist": Point(value=self.data.scale_words[k])})

    # ------------------------------------------------------------------ ages

    def _age_candidates(self, c: str) -> list[str]:
        """The phrase as said, then without trailing 'years old' and leading fillers ('in his', 'when I')."""
        toks = [_AGE_AF.get(t, t) for t in c.split()]
        out = [" ".join(toks)]
        while toks and toks[-1] in _AGE_TRAIL:
            toks = toks[:-1]
        out.append(" ".join(toks))
        while toks and toks[0] in _AGE_LEAD:
            toks = toks[1:]
            out.append(" ".join(toks))
        seen: set[str] = set()
        return [v for v in out if v and not (v in seen or seen.add(v))]

    def age_band(self, word: str) -> Lookup:
        """An age from a band or template: 'early sixties', 'late 50s', 'turning sixty', 'before fifty-five', 'grade nine'."""
        c = canon(word)
        sec = self.sections["age_offsets"]
        for v in self._age_candidates(c):
            key = self._exact("age_bands", v)
            if key:
                return Lookup(section="age_bands", phrase=word, key=key, match="exact", similarity=1.0, dist=self.data.age_bands[key].to_dist())
            n = parse_number(v)
            if n is not None:
                return Lookup(section="age_bands", phrase=word, match="exact", similarity=1.0, dist=Point(value=n), base_value=n, detail="number")
            t = sec.template(v)
            if t:
                key, n = t
                return Lookup(section="age_offsets", phrase=word, key=key, match="exact", similarity=1.0, dist=self.data.age_offsets[key].to_dist(offset=n),
                              base_value=n)  # fmt: skip
            ap = self._approx_number(v)
            if ap:
                key, n, _ = ap
                lo, mo, hi = self.data.approx_words[key].offsets()
                f = self._age_years_per_rel
                dist: Dist = Point(value=n) if (lo, hi) == (0, 0) else Triangular(low=n + lo * f, mode=n + mo * f, high=n + hi * f)
                return Lookup(section="approx_words", phrase=word, key=key, match="exact", similarity=1.0, dist=dist, base_value=n,
                              detail=f"age spread: {f:g} years per unit of relative half-width")  # fmt: skip
        cands = self._age_candidates(c)
        target = cands[-1] if cands else c
        return self._near_or_miss("age_bands", word, target, lambda k: {"dist": self.data.age_bands[k].to_dist()})

    # ------------------------------------------------------------------ relative, rate, likelihood

    def relative(self, word: str) -> Lookup:
        """A multiplier on the reference slot (§8.3 AMOUNT_RELATIVE): 'a bit less' → Triangular(0.75, 0.88, 0.97)."""
        c = canon(word)
        cands = [c]
        for sep in (" than ", " as ", " of "):
            if sep in f" {c} ":
                head = f" {c} ".split(sep, 1)[0].strip()
                if head:
                    cands.append(head)
        for v in cands:
            key = self._exact("relative_words", v)
            if key:
                return Lookup(section="relative_words", phrase=word, key=key, match="exact", similarity=1.0, dist=self.data.relative_words[key].to_dist())
            parts = split_number(v)
            if parts and parts[2] == "percent" and parts[0] in ("", "about", "around", "roughly"):
                n = parts[1] / 100.0
                dist = self.approx(parts[0], n).dist if parts[0] else Point(value=n)
                return Lookup(section="relative_words", phrase=word, match="exact", similarity=1.0, dist=dist, base_value=n, detail="percentage")
        return self._near_or_miss("relative_words", word, cands[-1], lambda k: {"dist": self.data.relative_words[k].to_dist()})

    def rate(self, word: str) -> Lookup:
        """An annual real rate (§8.2 rate_words): 'growing a bit' → Normal(0.03, 0.03); 'inflation plus two' → Normal(0.02, 0.01)."""
        c = canon(word)
        key = self._exact("rate_words", c)
        if key:
            return Lookup(section="rate_words", phrase=word, key=key, match="exact", similarity=1.0, dist=self.data.rate_words[key].to_dist())
        t = self.sections["rate_templates"].template(c)
        if t:
            key, n = t
            return Lookup(section="rate_templates", phrase=word, key=key, match="exact", similarity=1.0,
                          dist=Normal(mean=n / 100.0, sd=self.data.rate_templates[key].sd), base_value=n)  # fmt: skip
        parts = split_number(c)
        if parts and parts[2] in ("percent", "percent a year", "percent per year", "percent per annum"):
            n = parts[1] / 100.0
            if not parts[0]:
                return Lookup(section="rate_words", phrase=word, match="exact", similarity=1.0, dist=Point(value=n), base_value=n, detail="percentage")
            ap = self.approx(parts[0], n)
            if ap.match == "exact":
                return Lookup(section="approx_words", phrase=word, key=ap.key, match="exact", similarity=1.0, dist=ap.dist, base_value=n)
        return self._near_or_miss("rate_words", word, c, lambda k: {"dist": self.data.rate_words[k].to_dist()})

    def likelihood(self, word: str) -> Lookup:
        """P(event) (§8.2 likelihood_words): 'might' → mean 0.40 (0.25–0.55); '80 percent' → 0.8."""
        c = canon(word)
        key = self._exact("likelihood_words", c)
        if key:
            e = self.data.likelihood_words[key]
            return Lookup(section="likelihood_words", phrase=word, key=key, match="exact", similarity=1.0, likelihood=Likelihood(**e.model_dump()),
                          dist=Bernoulli(p=e.mean))  # fmt: skip
        p = _percent(c)
        if p is not None:
            lk = Likelihood(mean=p, low=max(0.0, p - 0.05), high=min(1.0, p + 0.05))
            return Lookup(section="likelihood_words", phrase=word, match="exact", similarity=1.0, likelihood=lk, dist=Bernoulli(p=p), detail="percentage")

        def make(k: str) -> dict[str, Any]:
            e = self.data.likelihood_words[k]
            return {"likelihood": Likelihood(**e.model_dump()), "dist": Bernoulli(p=e.mean)}

        return self._near_or_miss("likelihood_words", word, c, make)

    # ------------------------------------------------------------------ hedges and certainty

    def hedge_lookup(self, term: str) -> Lookup:
        """Hedge strength 0 (certain) .. 1 (pure speculation), a calibration feature (§7.3 step 5, §9.2)."""
        c = canon(term)
        if not c:
            return Lookup(section="hedge_strength", phrase=term, match="default", value=0.0, detail="no hedge")
        key = self._exact("hedge_strength", c)
        if key:
            return Lookup(section="hedge_strength", phrase=term, key=key, match="exact", similarity=1.0, value=self.data.hedge_strength[key])
        return self._near_or_miss("hedge_strength", term, c, lambda k: {"value": self.data.hedge_strength[k]},
                                  miss_value={"value": self.params.hedge_default_strength, "detail": "params.hedge_default_strength"})  # fmt: skip

    def hedge(self, term: str) -> Hedge:
        lk = self.hedge_lookup(term)
        return Hedge(term=term, strength=float(lk.value or 0.0), lexicon_key=lk.key or "")

    def certainty(self, word: str, *, kind: Literal["category", "boolean"] = "category") -> Lookup:
        """P(the stated answer is right) for CATEGORY and BOOLEAN forms; no word → the kind's default (§8.3)."""
        c = canon(word)
        d = self.params.certainty_default
        if not c:
            return Lookup(section="certainty_words", phrase=word, match="default", value=getattr(d, kind), detail=f"no certainty word: {kind} default")
        key = self._exact("certainty_words", c)
        if key:
            return Lookup(section="certainty_words", phrase=word, key=key, match="exact", similarity=1.0, value=self.data.certainty_words[key])
        p = _percent(c)
        if p is not None:
            return Lookup(section="certainty_words", phrase=word, match="exact", similarity=1.0, value=p, detail="percentage")
        if kind == "boolean":  # §8.3: BOOLEAN with a certainty word uses the likelihood table
            key = self._exact("likelihood_words", c)
            if key:
                return Lookup(section="likelihood_words", phrase=word, key=key, match="exact", similarity=1.0, value=self.data.likelihood_words[key].mean)
        return self._near_or_miss("certainty_words", word, c, lambda k: {"value": self.data.certainty_words[k]},
                                  miss_value={"value": d.unknown_word, "detail": "params.certainty_default.unknown_word"})  # fmt: skip

    def recency_weights(self) -> RecencyWeights:
        """Revising vs revised hypothesis weights (§7.3 step 7)."""
        return self.data.recency_weight

    # ------------------------------------------------------------------ categories

    def category_synonym(self, label: str, categories: Iterable[str]) -> str | None:
        """The slot category a spoken label stands for ('cautious' → 'moderately-conservative'), if the slot allows it."""
        allowed = {canon(cat): cat for cat in categories}
        for v in variants(canon(label)):
            for target in self._synonyms.get(v, []):
                if canon(target) in allowed:
                    return allowed[canon(target)]
        return None

    def ordered_scale(self, categories: Sequence[str]) -> list[str] | None:
        """The slot's categories in scale order when they form an ordered scale, else None (§8.3)."""
        cats = list(categories)
        if len(cats) >= 3 and all(parse_number(c) is not None and re.fullmatch(r"-?\d+(\.\d+)?", str(c).strip()) for c in cats):
            return sorted(cats, key=lambda c: float(c))
        by_canon = {canon(c): c for c in cats}
        for scale in self._scales:
            if {canon(s) for s in scale} == set(by_canon):
                return [by_canon[canon(s)] for s in scale]
        return None

    def __repr__(self) -> str:
        return f"Lexicon(version={self.version!r}, embedder={self.embedder.name!r})"


def _percent(c: str) -> float | None:
    """'80 percent', 'eighty percent sure', '80 percent chance', 'nine out of ten' → 0.8 / 0.9."""
    m = re.fullmatch(r"(.+?) out of (ten|10|a hundred|hundred|100)", c)
    if m:
        n = parse_number(m.group(1))
        d = 10.0 if m.group(2) in ("ten", "10") else 100.0
        return None if n is None or not 0 <= n <= d else n / d
    parts = split_number(c)
    if parts and not parts[0] and parts[2].split()[:1] == ["percent"] and 0 <= parts[1] <= 100:
        return parts[1] / 100.0
    return None


@functools.lru_cache(maxsize=8)
def _load(version: str) -> Lexicon:
    path = LEXICON_DIR / f"v{version}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no lexicon {version!r} at {path}")
    lex = Lexicon.from_path(path)
    if lex.version != version:
        raise ValueError(f"{path.name} declares version {lex.version!r}")
    return lex


def load_lexicon(version: str = CURRENT_VERSION, *, embedder: Embedder | None = None) -> Lexicon:
    """The lexicon for a pinned version, loaded once per version (§8.2). Pass the run's pinned embedder
    (``get_embedder(pins.embedding_model)``) for near-miss lookups; the default is the hashed embedder."""
    lex = _load(str(version))
    return lex.with_embedder(embedder) if embedder is not None else lex


def available_versions() -> list[str]:
    return sorted(p.stem[1:] for p in LEXICON_DIR.glob("v*.yaml"))


__all__ = [
    "CURRENT_VERSION",
    "LEXICON_DIR",
    "Lexicon",
    "LexiconFile",
    "Likelihood",
    "Lookup",
    "MissLog",
    "MissRecord",
    "RecencyWeights",
    "available_versions",
    "load_lexicon",
    "variants",
]
