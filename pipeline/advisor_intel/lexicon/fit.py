"""Lexicon re-fit from advisor edits (spec §11.4 item 2).

The nightly ``propose_lexicon_fit`` activity takes (phrase, intermediate form, advisor-edited
distribution) pairs. For every lexicon key with at least 20 pairs it fits the entry by maximising
the likelihood of the edited medians, in the entry's own units:

- approx words: the relative deviation of the edited median from the stated value (symmetric
  entries fit a half-width, asymmetric ones a triangular low/mode/high);
- vague counts and amounts, age bands and age templates: the edited median in the key's units
  (scale words, the period conversion, the duration anchor and the stated age are undone first);
- relative words: the edited median over the reference value's median;
- rate words: a normal's mean and sd; likelihood words: a Beta's mean and p10/p90 from the edited
  probabilities; certainty words: the probability the advisor kept on the stated answer.

The result is the new YAML text (one changed line per refitted key, comments and order kept, the
version bumped), a unified diff for the pull request, and a readable summary. A human merges it and
CI runs the golden cases (§8.4). The loaded lexicon is never mutated.
"""

from __future__ import annotations

import difflib
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np
from pydantic import BaseModel, TypeAdapter
from scipy import optimize
from scipy import stats as sps

from .. import stats
from ..models.dists import Dist, Quantity
from ..models.extraction import IntermediateForm
from ..models.facts import Fact
from .golden import GoldenCase, check_cases
from .loader import ApproxEntry, DistEntry, Lexicon, LikelihoodEntry, Lookup
from .phrases import canon

MIN_PAIRS = 20  # §11.4: a key is re-fitted once it has at least 20 pairs
FITTED_SECTIONS = (
    "approx_words",
    "vague_counts",
    "vague_amounts",
    "age_bands",
    "age_offsets",
    "relative_words",
    "rate_words",
    "likelihood_words",
    "certainty_words",
)
_FORM = TypeAdapter(IntermediateForm)
_Z90 = 1.2815515655446004
_FLOOR = {"approx_words": 0.005, "relative_words": 0.01, "age_bands": 0.25, "age_offsets": 0.25, "vague_counts": 0.05, "vague_amounts": 1.0}
_DURATION_NOISE = {
    "in",
    "from",
    "now",
    "time",
    "s",
    "years",
    "year",
    "months",
    "month",
    "jaar",
    "maande",
    "maand",
    "oor",
    "the",
    "next",
    "ago",
    "gelede",
}


class EditPair(BaseModel):
    """One advisor edit of a fact whose value came from a lexicon entry."""

    phrase: str  # the vague phrase as spoken
    form: IntermediateForm  # the intermediate form the extractor returned
    edited: Dist  # the advisor-edited distribution, in the slot's unit
    section: str | None = None  # the lexicon section and key the original lookup used (from Fact.normalization);
    key: str | None = None  # without them the phrase is looked up again and only exact hits count
    value_factor: float = 1.0  # spoken units → slot units: period conversion × deflation (normalization.value_factor)
    multiplier: float = 1.0  # composite vague amounts: the scale words' product
    base_value: float | None = None  # the stated number an approx word or age template applied to
    anchor_offset: float | None = None  # durations: the year, date or age the duration counted from
    reference_median: float | None = None  # AMOUNT_RELATIVE: the reference value's median in the slot's unit
    category: str | None = None  # CATEGORY: the slot category the label matched
    fna_unit: str | None = None

    @classmethod
    def from_fact(cls, fact: Fact, edited: Quantity | Dist) -> list[EditPair]:
        """Pairs from a reviewed fact: one per exact lexicon lookup recorded in its normalization.

        A fact with several hypotheses records one normalization per hypothesis; when more than one
        of them used the lexicon, the edit cannot be tied to a phrase, so the fact gives no pairs.
        """
        norm = fact.normalization or {}
        if "hypotheses" in norm:
            subs = [h for h in norm["hypotheses"] if any(r.get("match") == "exact" for r in h.get("lexicon", []))]
            if len(subs) != 1:
                return []
            norm = subs[0]
        if "form" not in norm:
            return []
        form = _FORM.validate_python(norm["form"])
        dist = edited.dist if isinstance(edited, Quantity) else edited
        out: list[EditPair] = []
        for rec in norm.get("lexicon", []):
            if rec.get("match") != "exact" or not rec.get("key") or rec.get("section") not in FITTED_SECTIONS:
                continue
            out.append(
                cls(
                    phrase=rec.get("phrase", ""),
                    form=form,
                    edited=dist,
                    section=rec["section"],
                    key=rec["key"],
                    value_factor=float(norm.get("value_factor") or 1.0),
                    multiplier=float(rec.get("multiplier") or 1.0),
                    base_value=rec.get("base_value"),
                    anchor_offset=norm.get("anchor_offset"),
                    reference_median=(norm.get("reference") or {}).get("median"),
                    category=norm.get("category"),
                    fna_unit=norm.get("fna_unit"),
                )
            )
        return out


class EntryFit(BaseModel):
    section: str
    key: str
    n_pairs: int
    old: dict[str, Any]
    new: dict[str, Any]
    mean_loglik_old: float | None = None  # on the observations, per pair
    mean_loglik_new: float | None = None


class LexiconDiff(BaseModel):
    base_version: str
    new_version: str
    min_pairs: int
    changes: list[EntryFit] = []
    skipped: dict[str, int] = {}  # "section: key" → pairs, below min_pairs
    not_fitted: dict[str, int] = {}  # keys with enough pairs whose entry is fixed (points, scale words)
    unmatched: dict[str, int] = {}  # phrases with no exact key: candidates for new keys (weekly review)
    yaml_text: str  # the proposed lexicon file
    diff_text: str  # unified diff against the base version
    golden_moved: list[str] = []  # golden cases (§8.4) the proposal would move by more than 10%

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    @property
    def new_path(self) -> str:
        return f"lexicon/v{self.new_version}.yaml"

    def summary(self) -> str:
        """A readable pull-request description."""
        lines = [f"Lexicon re-fit v{self.base_version} → v{self.new_version} from advisor edits (keys with >= {self.min_pairs} pairs)", ""]
        if not self.changes:
            lines.append("No entry changed.")
        for c in self.changes:
            ll = ""
            if c.mean_loglik_old is not None and c.mean_loglik_new is not None:
                ll = f"; mean log-likelihood {c.mean_loglik_old:.3f} → {c.mean_loglik_new:.3f}"
            lines.append(f"- {c.section}: {c.key!r} (n={c.n_pairs}){ll}")
            lines.append(f"    was {_flow(c.old)}")
            lines.append(f"    now {_flow(c.new)}")
        if self.skipped:
            lines += ["", "Too few pairs to re-fit: " + ", ".join(f"{k} ({n})" for k, n in self.skipped.items())]
        if self.not_fitted:
            lines += ["", "Fixed entries (not re-fitted): " + ", ".join(f"{k} ({n})" for k, n in self.not_fitted.items())]
        if self.unmatched:
            lines += ["", "Phrases with no lexicon key (weekly review): " + ", ".join(f"{k!r} ({n})" for k, n in self.unmatched.items())]
        if self.golden_moved:
            lines += ["", "Golden cases to update before merging (§8.4):", *(f"- {m}" for m in self.golden_moved)]
        return "\n".join(lines)


# --------------------------------------------------------------------------- maximum-likelihood estimators


def fit_symmetric_triangular(xs: Sequence[float], floor: float = 0.0) -> float:
    """Half-width h of Triangular(-h, 0, h) maximising Σ log((h − |x|) / h²)."""
    a = np.abs(np.asarray(xs, dtype=float))
    n, amax = len(a), float(a.max())

    def g(h: float) -> float:
        return float(np.sum(1.0 / (h - a)) - 2.0 * n / h)

    lo = amax * (1 + 1e-9) + 1e-12
    if g(lo) <= 0:
        return max(lo, floor)
    hi = max(2 * lo, 1e-6)
    for _ in range(200):
        if g(hi) < 0:
            break
        hi *= 2
    return max(float(optimize.brentq(g, lo, hi, xtol=1e-12)), floor)


def _tri_logpdf(x: np.ndarray, a: float, c: float, b: float) -> np.ndarray:
    out = np.full(x.shape, -np.inf)
    if b <= a:
        return out
    left = (x >= a) & (x <= c) & (c > a)
    right = (x > c) & (x <= b) & (b > c)
    out[left] = np.log(2 * (x[left] - a) / ((b - a) * (c - a)) + 1e-300)
    out[right] = np.log(2 * (b - x[right]) / ((b - a) * (b - c)) + 1e-300)
    out[x == c] = math.log(2 / (b - a))
    return out


_MODE_GRID = 50  # candidate modes searched before refining around the best one


def fit_triangular(xs: Sequence[float], floor: float = 0.0) -> tuple[float, float, float]:
    """(low, mode, high) maximising the triangular likelihood.

    The MLE mode is an order statistic, so modes are searched over the observations: a rank-spaced
    grid of at most 50 first, then every observation between the best one's grid neighbours. For each
    candidate mode the ends are optimised below the smallest and above the largest observation.
    """
    x = np.sort(np.asarray(xs, dtype=float))
    lo, hi = float(x[0]), float(x[-1])
    spread = hi - lo
    if spread <= max(floor, 1e-12):
        c = float(np.median(x))
        w = max(floor, 1e-9)
        return c - w, c, c + w

    n = len(x)

    def ends(c: float) -> tuple[float, float, float]:
        xl, xr = x[x <= c], x[x > c]
        nl, nr = len(xl), len(xr)

        def nll(p: np.ndarray) -> tuple[float, np.ndarray]:
            # a = lo − spread·e^p0 < every x, b = hi + spread·e^p1 > every x; the triangular log-likelihood
            # Σ log(2(x−a)/((b−a)(c−a))) on the left of the mode and Σ log(2(b−x)/((b−a)(b−c))) on the right.
            da, db = spread * math.exp(p[0]), spread * math.exp(p[1])
            a, b = lo - da, hi + db
            ll = n * math.log(2) - n * math.log(b - a) + np.log(xl - a).sum() - nl * math.log(c - a) + np.log(b - xr).sum()
            ll -= nr * math.log(b - c) if nr else 0.0
            d_a = -np.sum(1.0 / (xl - a)) + nl / (c - a) + n / (b - a)
            d_b = np.sum(1.0 / (b - xr)) - (nr / (b - c) if nr else 0.0) - n / (b - a)
            return -float(ll), -np.array([d_a * -da, d_b * db])

        res = optimize.minimize(nll, x0=np.array([-2.0, -2.0]), jac=True, method="L-BFGS-B", bounds=[(-14.0, 3.0), (-14.0, 3.0)])
        return float(res.fun), lo - spread * math.exp(res.x[0]), hi + spread * math.exp(res.x[1])

    uniq = np.unique(x)
    grid = np.unique(np.linspace(0, len(uniq) - 1, min(len(uniq), _MODE_GRID)).round().astype(int))
    scored = {int(i): ends(float(uniq[i])) for i in grid}
    best_i = min(scored, key=lambda i: scored[i][0])
    pos = int(np.searchsorted(grid, best_i))
    left, right = int(grid[max(0, pos - 1)]), int(grid[min(len(grid) - 1, pos + 1)])
    for i in range(left, right + 1):
        if i not in scored:
            scored[i] = ends(float(uniq[i]))
    best_i = min(scored, key=lambda i: scored[i][0])
    _, a, b = scored[best_i]
    c = float(uniq[best_i])
    if c - a < floor and b - c < floor:
        a, b = c - floor, c + floor
    return a, c, b


def fit_normal(xs: Sequence[float], sd_floor: float = 0.002) -> tuple[float, float]:
    x = np.asarray(xs, dtype=float)
    return float(x.mean()), max(sd_floor, float(x.std()))


def fit_lognormal(xs: Sequence[float]) -> tuple[float, float, float] | None:
    """(median, p10, p90) of the MLE lognormal, symmetric in log space as ``LogNormal`` stores it."""
    x = np.asarray([v for v in xs if v > 0], dtype=float)
    if x.size < 2:
        return None
    mu, sigma = float(np.log(x).mean()), max(0.01, float(np.log(x).std()))
    return math.exp(mu), math.exp(mu - _Z90 * sigma), math.exp(mu + _Z90 * sigma)


def fit_beta(ps: Sequence[float]) -> tuple[float, float, float]:
    """(mean, p10, p90) of the MLE Beta for edited event probabilities."""
    p = np.clip(np.asarray(ps, dtype=float), 0.005, 0.995)
    m = float(p.mean())
    if float(np.ptp(p)) < 1e-6:
        return m, max(0.0, m - 0.05), min(1.0, m + 0.05)
    try:
        a, b, _, _ = sps.beta.fit(p, floc=0, fscale=1)
    except Exception:  # noqa: BLE001  # pragma: no cover - any scipy fit failure falls back to moments
        v = float(p.var())
        k = m * (1 - m) / v - 1
        a, b = m * k, (1 - m) * k
    mean = a / (a + b)
    low, high = float(sps.beta.ppf(0.1, a, b)), float(sps.beta.ppf(0.9, a, b))
    return float(mean), min(low, mean), max(high, mean)


def fit_discrete(xs: Sequence[float], support: Sequence[float]) -> list[float]:
    """Weights on a fixed support: counts of the nearest support point, with add-½ smoothing."""
    sup = np.asarray(support, dtype=float)
    counts = np.full(len(sup), 0.5)
    for v in xs:
        counts[int(np.argmin(np.abs(sup - v)))] += 1
    return [float(c) for c in counts / counts.sum()]


# --------------------------------------------------------------------------- pairs → observations


def _relookup(pair: EditPair, lex: Lexicon) -> Lookup | None:
    f = pair.form
    t = f.type
    if t == "AMOUNT_APPROX":
        return lex.approx(f.approx_word, f.value)  # type: ignore[union-attr]
    if t == "AMOUNT_VAGUE":
        return lex.vague_amount(f.vague_word)  # type: ignore[union-attr]
    if t == "AMOUNT_RELATIVE":
        return lex.relative(f.relation_word)  # type: ignore[union-attr]
    if t == "AGE" and f.form == "vague":  # type: ignore[union-attr]
        return lex.age_band(f.vague_word)  # type: ignore[union-attr]
    if t == "RATE" and f.form == "vague":  # type: ignore[union-attr]
        return lex.rate(f.vague_word)  # type: ignore[union-attr]
    if t == "DURATION_FROM_ANCHOR" and f.form == "vague":  # type: ignore[union-attr]
        words = [w for w in canon(f.vague_word).split() if w not in _DURATION_NOISE]  # type: ignore[union-attr]
        return lex.vague_count(" ".join(words) or f.vague_word)  # type: ignore[union-attr]
    if t == "PROBABILITY_EVENT":
        return lex.likelihood(f.likelihood_word)  # type: ignore[union-attr]
    if t in ("CATEGORY", "BOOLEAN") and f.certainty_word:  # type: ignore[union-attr]
        return lex.certainty(f.certainty_word, kind="boolean" if t == "BOOLEAN" else "category")  # type: ignore[union-attr]
    return None


def observation(pair: EditPair, lex: Lexicon) -> tuple[str, str, float] | None:
    """(section, key, observation in the entry's own units) for one pair, or None when it informs no key."""
    section, key, base, mult = pair.section, pair.key, pair.base_value, pair.multiplier
    if not section or not key:
        lk = _relookup(pair, lex)
        if lk is None or lk.match != "exact" or not lk.key:
            return None
        section, key = lk.section, lk.key
        base = base if base is not None else lk.base_value
        mult = mult if mult != 1.0 else lk.multiplier
    if section not in FITTED_SECTIONS:
        return None
    d = pair.edited
    if section == "likelihood_words":
        return (section, key, stats.p_true(d)) if stats.is_boolean(d) else None
    if section == "certainty_words":
        if pair.form.type == "BOOLEAN" and stats.is_boolean(d):
            p = stats.p_true(d)
            return section, key, p if pair.form.value else 1.0 - p  # type: ignore[union-attr]
        if stats.is_categorical(d):
            named = pair.category or getattr(pair.form, "label", None)
            return (section, key, stats.categorical_probs(d).get(named, 0.0)) if named else None
        return None
    if stats.is_categorical(d) or stats.is_boolean(d):
        return None
    m = stats.median(d)
    if section == "approx_words":
        b = base if base is not None else getattr(pair.form, "value", None)
        if not b:
            return None
        return section, key, m / (b * pair.value_factor * mult) - 1.0
    if section == "vague_counts":
        return section, key, (m - (pair.anchor_offset or 0.0)) / (pair.value_factor * mult)
    if section == "vague_amounts":
        return section, key, m / pair.value_factor
    if section == "age_bands":
        return (section, key, m) if pair.fna_unit in (None, "age") else None
    if section == "age_offsets":
        return (section, key, m - base) if base is not None and pair.fna_unit in (None, "age") else None
    if section == "relative_words":
        return (section, key, m / pair.reference_median) if pair.reference_median else None
    return section, key, m  # rate_words


# --------------------------------------------------------------------------- entries


def _entry_dict(entry: Any) -> dict[str, Any] | float:
    if isinstance(entry, (int, float)):
        return float(entry)
    return entry.model_dump(exclude_none=True)


def fit_entry(section: str, entry: Any, xs: Sequence[float]) -> dict[str, Any] | float | None:
    """The re-fitted entry in the lexicon's YAML shape, or None for entries that stay fixed."""
    floor = max(_FLOOR.get(section, 0.0), 0.01 * abs(float(np.median(xs))) if section not in ("approx_words", "rate_words") else 0.0)
    if section == "certainty_words":
        return float(np.clip(np.mean(xs), 0.01, 0.999))
    if isinstance(entry, LikelihoodEntry):
        mean, low, high = fit_beta(xs)
        return {"mean": mean, "low": low, "high": high}
    if isinstance(entry, ApproxEntry):
        if entry.dist == "point":
            return None
        if entry.rel_halfwidth is not None:
            return {"dist": "triangular", "rel_halfwidth": fit_symmetric_triangular(xs, floor)}
        a, c, b = fit_triangular(xs, floor)
        return {"dist": "triangular", "rel_low": a, "rel_mode": c, "rel_high": b}
    if isinstance(entry, DistEntry):
        if entry.dist == "point":
            return None
        if entry.dist == "triangular":
            a, c, b = fit_triangular(xs, floor)
            return {"dist": "triangular", "low": a, "mode": c, "high": b}
        if entry.dist == "discrete":
            return {"dist": "discrete", "support": list(entry.support or []), "weights": fit_discrete(xs, entry.support or [])}
        if entry.dist == "normal":
            mean, sd = fit_normal(xs)
            return {"dist": "normal", "mean": mean, "sd": sd}
        if entry.dist == "lognormal":
            fit = fit_lognormal(xs)
            return None if fit is None else {"dist": "lognormal", "median": fit[0], "p10": fit[1], "p90": fit[2]}
    return None


def _mean_loglik(section: str, entry: dict[str, Any] | float, xs: Sequence[float]) -> float | None:
    x = np.asarray(xs, dtype=float)
    if isinstance(entry, float):
        p = min(max(entry, 1e-6), 1 - 1e-6)
        return float(np.mean(x * math.log(p) + (1 - x) * math.log(1 - p)))
    kind = entry.get("dist")
    if kind == "triangular":
        if "rel_halfwidth" in entry:
            h = entry["rel_halfwidth"]
            a, c, b = -h, 0.0, h
        elif "rel_low" in entry:
            a, c, b = entry["rel_low"], entry["rel_mode"], entry["rel_high"]
        else:
            a, c, b = entry["low"], entry["mode"], entry["high"]
        return float(np.mean(np.maximum(_tri_logpdf(x, a, c, b), math.log(1e-12))))
    if kind == "normal":
        return float(np.mean(sps.norm.logpdf(x, entry["mean"], max(entry["sd"], 1e-9))))
    if kind == "lognormal" and np.all(x > 0):
        sigma = max(math.log(entry["median"] / entry["p10"]), math.log(entry["p90"] / entry["median"])) / _Z90
        return float(np.mean(sps.lognorm.logpdf(x, s=max(sigma, 1e-9), scale=entry["median"])))
    if kind == "discrete":
        sup = np.asarray(entry["support"], dtype=float)
        w = np.asarray(entry["weights"], dtype=float)
        return float(np.mean([math.log(max(w[int(np.argmin(np.abs(sup - v)))], 1e-12)) for v in x]))
    return None


# --------------------------------------------------------------------------- YAML text


def _num(x: float) -> str:
    """4 significant digits, never in exponent form (PyYAML reads '1e+06' as a string)."""
    if x == 0 or not math.isfinite(x):
        return "0"
    digits = 3 - math.floor(math.log10(abs(x)))
    return np.format_float_positional(round(x, digits), trim="-")


def _flow(entry: dict[str, Any] | float) -> str:
    if isinstance(entry, (int, float)):
        return _num(float(entry))

    def val(v: Any) -> str:
        if isinstance(v, (list, tuple)):
            return "[" + ", ".join(_num(float(x)) for x in v) + "]"
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return _num(float(v))
        return str(v)

    return "{" + ", ".join(f"{k}: {val(v)}" for k, v in entry.items()) + "}"


_SECTION = re.compile(r"^([A-Za-z_]\w*):")
_ENTRY = re.compile(r'^(?P<indent>\s+)(?P<q>"?)(?P<key>[^"#:]+?)(?P=q):\s*(?P<value>\{[^}]*\}|[-+0-9.]+)\s*(?P<comment>#.*)?$')


def replace_entry(text: str, section: str, key: str, value_text: str, note: str) -> str:
    """Replace one entry's line in the lexicon YAML, keeping everything else byte for byte."""
    lines = text.splitlines(keepends=True)
    current = None
    for i, line in enumerate(lines):
        m = _SECTION.match(line)
        if m:
            current = m.group(1)
            continue
        if current != section:
            continue
        e = _ENTRY.match(line.rstrip("\n"))
        if e and e.group("key").strip() == key:
            q = e.group("q")
            lines[i] = f"{e.group('indent')}{q}{key}{q}: {value_text}  # {note}\n"
            return "".join(lines)
    raise KeyError(f"{section}: {key!r} not found in the lexicon text")


def bump_version(version: str) -> str:
    parts = version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def _same_entry(old: dict[str, Any] | float, new: dict[str, Any] | float, rel: float = 0.01) -> bool:
    def flat(e: dict[str, Any] | float) -> list[float]:
        if isinstance(e, (int, float)):
            return [float(e)]
        out: list[float] = []
        for v in e.values():
            if isinstance(v, (list, tuple)):
                out += [float(x) for x in v]
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(float(v))
        return out

    a, b = flat(old), flat(new)
    return len(a) == len(b) and all(abs(x - y) <= max(rel * abs(x), 1e-4) for x, y in zip(a, b))


def propose_lexicon_fit(
    pairs: Iterable[EditPair | tuple[str, Any, Any]],
    lex: Lexicon,
    *,
    min_pairs: int = MIN_PAIRS,
    new_version: str | None = None,
    golden_cases: Sequence[GoldenCase] | None = None,
) -> LexiconDiff:
    """Re-fit every lexicon key with ``min_pairs`` or more advisor edits; return the proposed YAML and its diff (§11.4)."""
    work = Lexicon.from_text(lex.source_text)  # a private copy: the loaded lexicon and its miss log stay untouched
    new_version = new_version or bump_version(lex.version)
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    unmatched: dict[str, int] = defaultdict(int)
    for p in pairs:
        if not isinstance(p, EditPair):
            phrase, form, edited = p
            p = EditPair(
                phrase=phrase,
                form=_FORM.validate_python(form) if isinstance(form, dict) else form,
                edited=edited.dist if isinstance(edited, Quantity) else edited,
            )
        obs = observation(p, work)
        if obs is None or not math.isfinite(obs[2]):
            unmatched[canon(p.phrase)] += 1
            continue
        groups[(obs[0], obs[1])].append(obs[2])
    text = lex.source_text
    changes: list[EntryFit] = []
    skipped: dict[str, int] = {}
    not_fitted: dict[str, int] = {}
    for (section, key), xs in sorted(groups.items()):
        label = f"{section}: {key}"
        if len(xs) < min_pairs:
            skipped[label] = len(xs)
            continue
        entry = work.entry(section, key)
        new = fit_entry(section, entry, xs)
        if new is None:
            not_fitted[label] = len(xs)
            continue
        old = _entry_dict(entry)
        if _same_entry(old, new):
            continue
        rounded = new if isinstance(new, float) else {k: v for k, v in new.items()}
        text = replace_entry(text, section, key, _flow(rounded), f"re-fit in v{new_version} from {len(xs)} advisor edits")
        changes.append(
            EntryFit(
                section=section,
                key=key,
                n_pairs=len(xs),
                old=old if isinstance(old, dict) else {"value": old},
                new=new if isinstance(new, dict) else {"value": new},
                mean_loglik_old=_mean_loglik(section, old, xs),
                mean_loglik_new=_mean_loglik(section, new, xs),
            )
        )
    if changes:
        text = re.sub(r"^version:\s*.*$", f'version: "{new_version}"', text, count=1, flags=re.MULTILINE)
        proposed = Lexicon.from_text(text)  # the proposal must load and validate
    else:
        new_version, proposed = lex.version, work
    diff = "".join(
        difflib.unified_diff(
            lex.source_text.splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile=f"lexicon/v{lex.version}.yaml",
            tofile=f"lexicon/v{new_version}.yaml",
        )
    )
    moved = check_cases(proposed, golden_cases) if golden_cases and changes else []
    return LexiconDiff(
        base_version=lex.version,
        new_version=new_version,
        min_pairs=min_pairs,
        changes=changes,
        skipped=skipped,
        not_fitted=not_fitted,
        unmatched=dict(sorted(unmatched.items(), key=lambda kv: -kv[1])),
        yaml_text=text,
        diff_text=diff,
        golden_moved=moved,
    )


__all__ = [
    "MIN_PAIRS",
    "EditPair",
    "EntryFit",
    "LexiconDiff",
    "bump_version",
    "fit_beta",
    "fit_discrete",
    "fit_entry",
    "fit_lognormal",
    "fit_normal",
    "fit_symmetric_triangular",
    "fit_triangular",
    "observation",
    "propose_lexicon_fit",
    "replace_entry",
]
