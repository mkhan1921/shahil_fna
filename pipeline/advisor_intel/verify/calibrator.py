"""The calibrator: features → ``p_raw`` → ``p_correct`` (spec §9.4).

- **Cold start** (fewer than 300 labels, or fewer than 30 calls): isotonic regression on one hand-weighted
  score ``s = 0.4·entail_score + 0.3·consistency + 0.2·self_score + 0.1·asr_mean_conf``, NaNs replaced by
  the group's means. One map per slot group with ≥ 50 labels; smaller groups pool by slot family (§9.5);
  a family with fewer than 50 labels falls back to the pooled map.
- **Warm** (≥ 300 labels and ≥ 30 calls): ``HistGradientBoostingClassifier`` on the full feature vector
  (plus the cold-start score as one more column), then isotonic recalibration on a held-out fold of calls.
  The booster/map pairs are cross-fitted over 4 folds of calls and averaged, so every label serves
  calibration and no map sees its booster's training calls. One model per slot group with ≥ 200 labels;
  the rest share a pooled model, which sees the slot-group one-hots.
- **Venn-Abers**: every fit also fits an inductive Venn-Abers predictor on the same scores; it replaces
  isotonic for a group when its held-out Brier score and ECE are both better.
- **Splits are by call, never by fact** (``GroupKFold`` over call ids), so one call's facts never sit in
  both train and test. Held-out metrics come from out-of-fold predictions.
- **Metrics** per group and overall: ECE over 15 equal-mass bins, Brier score, AUROC and the
  reliability-diagram data.
- **Retraining** is due every 200 new labels or on any change to the extraction model, a prompt version or
  the ASR; a new calibrator is promoted only if its held-out ECE is not worse than the incumbent's by more
  than 0.01 (``retrain``).

The artifact serialises to bytes with a format version (``to_bytes`` / ``from_bytes``) for object storage.
"""

from __future__ import annotations

import hashlib
import io
import math
import pickle
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from threadpoolctl import threadpool_limits
from venn_abers import VennAbers

from ..models.runs import VersionPins
from ..schema.slots import Catalog, load_catalog
from .features import feature_names, vector
from .labels import Label

COLD_WEIGHTS: dict[str, float] = {"entail_score": 0.4, "consistency": 0.3, "self_score": 0.2, "asr_mean_conf": 0.1}
COLD_MIN_GROUP = 50
WARM_MIN_LABELS = 300
WARM_MIN_CALLS = 30
WARM_MIN_GROUP = 200
N_BINS = 15
N_FOLDS = 5
PROMOTION_TOL = 0.01
RETRAIN_EVERY = 200
MIN_NEW_FOR_COMPARISON = 50
EPS = 1e-3  # calibrated probabilities are kept inside [0.001, 0.999]
ARTIFACT_MAGIC = b"AICAL"
ARTIFACT_FORMAT = 1
Mode = Literal["identity", "cold", "warm"]
MapKind = Literal["isotonic", "venn_abers"]


# --------------------------------------------------------------------------- data


@dataclass
class LabelSet:
    """Labels as arrays: one row per labelled fact, in label order."""

    X: np.ndarray
    y: np.ndarray
    groups: np.ndarray
    families: np.ndarray
    calls: np.ndarray
    names: list[str]

    @classmethod
    def from_labels(cls, labels: Sequence[Label], names: list[str]) -> "LabelSet":
        X = np.vstack([vector(lb.features, names) for lb in labels]) if labels else np.zeros((0, len(names)))
        return cls(
            X=X,
            y=np.asarray([lb.correct for lb in labels], dtype=float),
            groups=np.asarray([lb.slot_group for lb in labels], dtype=object),
            families=np.asarray([lb.family for lb in labels], dtype=object),
            calls=np.asarray([lb.call_id for lb in labels], dtype=object),
            names=names,
        )

    def take(self, idx: np.ndarray) -> "LabelSet":
        return LabelSet(self.X[idx], self.y[idx], self.groups[idx], self.families[idx], self.calls[idx], self.names)

    def __len__(self) -> int:
        return len(self.y)

    @property
    def n_calls(self) -> int:
        return len(set(self.calls.tolist()))


# --------------------------------------------------------------------------- metrics


def ece(p: np.ndarray, y: np.ndarray, n_bins: int = N_BINS) -> float:
    """Expected calibration error over ``n_bins`` equal-mass bins."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    n = len(p)
    if n == 0:
        return math.nan
    order = np.argsort(p, kind="stable")
    return float(sum(len(b) / n * abs(p[b].mean() - y[b].mean()) for b in np.array_split(order, min(n_bins, n)) if len(b)))


def reliability(p: np.ndarray, y: np.ndarray, n_bins: int = N_BINS) -> list[dict[str, float]]:
    """Reliability-diagram data: per equal-mass bin, mean prediction, observed frequency and count."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    if len(p) == 0:
        return []
    order = np.argsort(p, kind="stable")
    return [
        {"mean_p": float(p[b].mean()), "frac_correct": float(y[b].mean()), "count": int(len(b))}
        for b in np.array_split(order, min(n_bins, len(p)))
        if len(b)
    ]


def brier(p: np.ndarray, y: np.ndarray) -> float:
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    return float(np.mean((p - y) ** 2)) if len(p) else math.nan


def auroc(p: np.ndarray, y: np.ndarray) -> float:
    y = np.asarray(y, dtype=float)
    return float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else math.nan


def calibration_metrics(p: np.ndarray, y: np.ndarray, n_bins: int = N_BINS) -> dict[str, Any]:
    return {
        "n": int(len(y)),
        "base_rate": float(np.mean(y)) if len(y) else math.nan,
        "ece": ece(p, y, n_bins),
        "brier": brier(p, y),
        "auroc": auroc(p, y),
        "reliability": reliability(p, y, n_bins),
    }


# --------------------------------------------------------------------------- splits by call


def call_folds(calls: np.ndarray, n_folds: int = N_FOLDS) -> list[tuple[np.ndarray, np.ndarray]]:
    """(train, test) row indices with whole calls on one side (``GroupKFold``)."""
    k = min(n_folds, len(set(calls.tolist())))
    if k < 2:
        return []
    return list(GroupKFold(n_splits=k).split(np.zeros(len(calls)), groups=calls))


# --------------------------------------------------------------------------- score maps and units


def cold_scores(X: np.ndarray, names: list[str], fill: Mapping[str, float]) -> np.ndarray:
    """The hand-weighted cold-start score, with each component's NaNs replaced by ``fill``."""
    s = np.zeros(len(X))
    for k, w in COLD_WEIGHTS.items():
        col = X[:, names.index(k)] if k in names else np.full(len(X), np.nan)
        s += w * np.where(np.isnan(col), fill.get(k, 0.5), col)
    return np.clip(s, 0.0, 1.0)


def component_means(X: np.ndarray, names: list[str], fallback: Mapping[str, float] | None = None) -> dict[str, float]:
    out = {}
    for k in COLD_WEIGHTS:
        col = X[:, names.index(k)] if (k in names and len(X)) else np.array([])
        vals = col[~np.isnan(col)] if col.size else col
        out[k] = float(vals.mean()) if vals.size else float((fallback or {}).get(k, 0.5))
    return out


class ScoreMap:
    """An isotonic or inductive Venn-Abers map from a raw score to a probability."""

    def __init__(self, kind: MapKind) -> None:
        self.kind = kind
        self.iso: IsotonicRegression | None = None
        self.va: VennAbers | None = None
        self.const: float | None = None

    @classmethod
    def fit(cls, kind: MapKind, s: np.ndarray, y: np.ndarray) -> "ScoreMap":
        m = cls(kind)
        s, y = np.asarray(s, dtype=float), np.asarray(y, dtype=float)
        if len(np.unique(y)) < 2:
            m.const = float(np.clip(y.mean() if len(y) else 0.5, EPS, 1 - EPS))
        elif kind == "isotonic":
            m.iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(s, y)
        else:
            m.va = VennAbers().fit(np.column_stack([1 - s, s]), y.astype(int))
        return m

    def predict(self, s: np.ndarray) -> np.ndarray:
        s = np.asarray(s, dtype=float)
        if self.const is not None:
            return np.full(len(s), self.const)
        if self.iso is not None:
            p = self.iso.predict(s)
        else:
            p_prime, _ = self.va.predict_proba(np.column_stack([1 - s, s]))  # type: ignore[union-attr]
            p = p_prime[:, 1]
        return np.clip(np.nan_to_num(p, nan=0.5), EPS, 1 - EPS)


@dataclass
class Member:
    """One cross-fitted pair (warm): a booster, and maps fit on calls the booster never saw."""

    hgb: HistGradientBoostingClassifier
    maps: dict[str, ScoreMap]
    fit_calls: frozenset[str]
    cal_calls: frozenset[str]


@dataclass
class Unit:
    """One fitted model: a slot group's, a slot family's, or the pooled one.

    Cold: one map over the cold-start score. Warm: ``CAL_FOLDS`` cross-fitted members, each a booster
    trained on all but one fold of calls with its isotonic / Venn-Abers map fit on the held-out fold;
    predictions average the members, so every label serves calibration without any map seeing its
    booster's training calls.
    """

    key: str  # "group:income", "family:household", "pooled"
    mode: Literal["cold", "warm"]
    fill: dict[str, float]
    dead: np.ndarray | None = None  # columns with no value at all in training: held constant for the booster
    members: list[Member] = field(default_factory=list)
    maps: dict[str, ScoreMap] = field(default_factory=dict)
    chosen: MapKind = "isotonic"
    n_labels: int = 0
    n_calls: int = 0
    heldout: dict[str, Any] = field(default_factory=dict)

    def boost_input(self, X: np.ndarray, names: list[str]) -> np.ndarray:
        """The booster's input: the full vector plus the cold-start score as one more column, so boosting
        starts from the hand-weighted baseline. A feature never observed in training (e.g. ``minicheck_score``
        without a MiniCheck deployment) is held at 0, so it neither breaks binning nor shifts predictions later."""
        if self.dead is not None and self.dead.any():
            X = X.copy()
            X[:, self.dead] = 0.0
        return np.column_stack([X, cold_scores(X, names, self.fill)])

    def predict(self, X: np.ndarray, names: list[str], kind: MapKind | None = None) -> tuple[np.ndarray, np.ndarray]:
        """(p_raw, p_correct): the raw score and the calibrated probability under map ``kind`` (default: the chosen one)."""
        kind = kind or self.chosen
        if self.mode == "warm" and self.members:
            inp = self.boost_input(X, names)
            raws, ps = [], []
            with one_thread():
                for m in self.members:
                    r = m.hgb.predict_proba(inp)[:, 1]
                    raws.append(r)
                    ps.append((m.maps.get(kind) or m.maps["isotonic"]).predict(r))
            return np.mean(raws, axis=0), np.mean(ps, axis=0)
        raw = cold_scores(X, names, self.fill)
        m = self.maps.get(kind) or self.maps.get("isotonic")
        return raw, (m.predict(raw) if m is not None else np.clip(raw, EPS, 1 - EPS))


CAL_FOLDS = 4  # warm: cross-fitted booster / map pairs per unit


def one_thread() -> threadpool_limits:
    """Boosting on calibration-sized data runs single-threaded: OpenMP threads buy nothing at this size and
    oversubscribe a worker that runs other activities (in some containers they stall outright)."""
    return threadpool_limits(limits=1, user_api="openmp")


def _hgb(seed: int) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=100, learning_rate=0.1, max_leaf_nodes=15, min_samples_leaf=20, l2_regularization=1.0, random_state=seed
    )


def fit_unit(
    data: LabelSet, key: str, mode: Literal["cold", "warm"], *, global_fill: Mapping[str, float], seed: int = 0, cal_folds: int = CAL_FOLDS
) -> Unit:
    fill = component_means(data.X, data.names, global_fill)
    unit = Unit(key=key, mode=mode, fill=fill, n_labels=len(data), n_calls=data.n_calls)
    if mode == "warm":
        unit.dead = np.all(np.isnan(data.X), axis=0) if len(data) else None
        folds = call_folds(data.calls, cal_folds)
        for fit_idx, cal_idx in folds:
            if len(np.unique(data.y[fit_idx])) < 2:
                continue
            with one_thread():
                hgb = _hgb(seed).fit(unit.boost_input(data.X[fit_idx], data.names), data.y[fit_idx])
                s, y = hgb.predict_proba(unit.boost_input(data.X[cal_idx], data.names))[:, 1], data.y[cal_idx]
            unit.members.append(
                Member(
                    hgb=hgb,
                    maps={"isotonic": ScoreMap.fit("isotonic", s, y), "venn_abers": ScoreMap.fit("venn_abers", s, y)},
                    fit_calls=frozenset(map(str, data.calls[fit_idx])),
                    cal_calls=frozenset(map(str, data.calls[cal_idx])),
                )
            )
        if unit.members:
            return unit
        unit.mode = "cold"  # too few calls, or one outcome only: the cold score still ranks
    s = cold_scores(data.X, data.names, fill)
    unit.maps = {"isotonic": ScoreMap.fit("isotonic", s, data.y), "venn_abers": ScoreMap.fit("venn_abers", s, data.y)}
    return unit


@dataclass
class Routing:
    """Which unit serves a fact: its slot group's, else its family's, else the pooled one."""

    group_units: set[str] = field(default_factory=set)
    family_units: set[str] = field(default_factory=set)

    def key(self, group: str, family: str) -> str:
        if group in self.group_units:
            return f"group:{group}"
        if family in self.family_units:
            return f"family:{family}"
        return "pooled"


def plan(data: LabelSet, mode: Literal["cold", "warm"], *, min_group: int | None = None) -> Routing:
    """§9.4 grouping: cold — group ≥ 50 labels, else family ≥ 50, else pooled; warm — group ≥ 200, else pooled."""
    min_group = min_group or (COLD_MIN_GROUP if mode == "cold" else WARM_MIN_GROUP)
    groups, counts = np.unique(data.groups, return_counts=True) if len(data) else (np.array([]), np.array([]))
    big = {str(g) for g, c in zip(groups, counts) if c >= min_group}
    routing = Routing(group_units=big)
    if mode == "cold":
        small_families = {str(f) for g, f in zip(data.groups, data.families) if str(g) not in big}
        for fam in small_families:
            if int(np.sum(data.families == fam)) >= COLD_MIN_GROUP:
                routing.family_units.add(fam)
    return routing


def fit_units(
    data: LabelSet, mode: Literal["cold", "warm"], *, global_fill: Mapping[str, float], seed: int = 0, cal_folds: int = CAL_FOLDS
) -> tuple[dict[str, Unit], Routing]:
    routing = plan(data, mode)
    units: dict[str, Unit] = {}
    kw = {"global_fill": global_fill, "seed": seed, "cal_folds": cal_folds}
    for g in routing.group_units:
        units[f"group:{g}"] = fit_unit(data.take(np.flatnonzero(data.groups == g)), f"group:{g}", mode, **kw)  # type: ignore[arg-type]
    for fam in routing.family_units:
        units[f"family:{fam}"] = fit_unit(data.take(np.flatnonzero(data.families == fam)), f"family:{fam}", mode, **kw)  # type: ignore[arg-type]
    units["pooled"] = fit_unit(data, "pooled", mode, **kw)  # type: ignore[arg-type]
    return units, routing


def _route(units: dict[str, Unit], routing: Routing, data: LabelSet) -> dict[str, np.ndarray]:
    """Row indices per serving unit (a routed key without a fitted unit falls back to the pooled one)."""
    keys = [routing.key(str(g), str(f)) for g, f in zip(data.groups, data.families)]
    keys = np.asarray([k if k in units else "pooled" for k in keys], dtype=object)
    return {k: np.flatnonzero(keys == k) for k in set(keys.tolist())}


def choose_map(iso: Mapping[str, float], va: Mapping[str, float]) -> MapKind:
    """Venn-Abers replaces isotonic only when its held-out Brier score and ECE are both better (§9.4)."""
    if any(not math.isfinite(m.get(k, math.nan)) for m in (iso, va) for k in ("brier", "ece")):
        return "isotonic"
    return "venn_abers" if va["brier"] < iso["brier"] and va["ece"] < iso["ece"] else "isotonic"


# --------------------------------------------------------------------------- the calibrator


@dataclass
class Calibrator:
    version: str
    mode: Mode
    names: list[str]
    units: dict[str, Unit] = field(default_factory=dict)
    routing: Routing = field(default_factory=Routing)
    global_fill: dict[str, float] = field(default_factory=lambda: {k: 0.5 for k in COLD_WEIGHTS})
    n_labels: int = 0
    n_calls: int = 0
    trained_on: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    oof: np.ndarray | None = None  # held-out predictions in label order, with each unit's chosen map
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def identity(cls, catalog: Catalog | None = None, version: str = "cold-start") -> "Calibrator":
        """Before any labels: ``p_correct`` is the cold-start score itself."""
        return cls(version=version, mode="identity", names=feature_names(catalog or load_catalog()))

    def unit_for(self, group: str, family: str) -> Unit | None:
        return self.units.get(self.routing.key(group, family)) or self.units.get("pooled")

    def predict(self, rows: Sequence[tuple[Mapping[str, float | None], str, str]]) -> tuple[np.ndarray, np.ndarray]:
        """(p_raw, p_correct) for (features, slot group, family) rows."""
        if not rows:
            return np.zeros(0), np.zeros(0)
        X = np.vstack([vector(f, self.names) for f, _, _ in rows])
        raw, p = np.zeros(len(rows)), np.zeros(len(rows))
        if self.mode == "identity" or not self.units:
            raw = cold_scores(X, self.names, self.global_fill)
            return raw, np.clip(raw, EPS, 1 - EPS)
        keys = [self.routing.key(g, fam) for _, g, fam in rows]
        keys = [k if k in self.units else "pooled" for k in keys]
        for key in set(keys):
            idx = np.asarray([i for i, k in enumerate(keys) if k == key])
            r, q = self.units[key].predict(X[idx], self.names)
            raw[idx], p[idx] = r, q
        return raw, p

    def predict_one(self, feats: Mapping[str, float | None], group: str, family: str) -> tuple[float, float]:
        raw, p = self.predict([(feats, group, family)])
        return float(raw[0]), float(p[0])

    def evaluate(self, labels: Sequence[Label]) -> dict[str, Any]:
        """Metrics of this calibrator's predictions on ``labels`` (overall and per slot group)."""
        _, p = self.predict([(lb.features, lb.slot_group, lb.family) for lb in labels])
        y = np.asarray([lb.correct for lb in labels], dtype=float)
        groups = np.asarray([lb.slot_group for lb in labels], dtype=object)
        return {
            "overall": calibration_metrics(p, y),
            "groups": {str(g): calibration_metrics(p[groups == g], y[groups == g]) for g in set(groups.tolist())},
        }

    # -- the artifact

    def to_bytes(self) -> bytes:
        payload = pickle.dumps({"format": ARTIFACT_FORMAT, "calibrator": self}, protocol=pickle.HIGHEST_PROTOCOL)
        return ARTIFACT_MAGIC + bytes([ARTIFACT_FORMAT]) + zlib.compress(payload)

    @staticmethod
    def from_bytes(blob: bytes) -> "Calibrator":
        """Load an artifact this module wrote (from the pipeline's own object storage only: it is a pickle)."""
        if not blob.startswith(ARTIFACT_MAGIC) or len(blob) < len(ARTIFACT_MAGIC) + 1:
            raise ValueError("not a calibrator artifact")
        fmt = blob[len(ARTIFACT_MAGIC)]
        if fmt != ARTIFACT_FORMAT:
            raise ValueError(f"calibrator artifact format {fmt} is not supported (expected {ARTIFACT_FORMAT})")
        data = pickle.load(io.BytesIO(zlib.decompress(blob[len(ARTIFACT_MAGIC) + 1 :])))  # noqa: S301 - our own artifact
        cal = data["calibrator"]
        if not isinstance(cal, Calibrator):
            raise ValueError("artifact does not hold a calibrator")
        return cal

    def experiment(self) -> dict[str, Any]:
        """The record for the Braintrust experiment ``calibration/<version>`` (metrics and reliability diagram)."""
        return {
            "name": f"calibration/{self.version}",
            "mode": self.mode,
            "n_labels": self.n_labels,
            "n_calls": self.n_calls,
            "metrics": self.metrics,
            "units": {k: {"chosen": u.chosen, "n_labels": u.n_labels, "heldout": u.heldout} for k, u in self.units.items()},
        }


def _trained_on(pins: VersionPins | None) -> dict[str, Any]:
    if pins is None:
        return {}
    return {
        "reasoning_model": pins.reasoning_model,
        "prompt_versions": dict(pins.prompt_versions),
        "asr_provider": pins.asr_provider,
        "asr_model_version": pins.asr_model_version,
    }


def _version(mode: str, labels: Sequence[Label]) -> str:
    h = hashlib.sha256()
    for lb in labels:
        h.update(f"{lb.call_id}|{lb.fact_id}|{lb.correct};".encode())
    return f"{mode}-{len(labels)}-{h.hexdigest()[:10]}"


def fit_calibrator(
    labels: Sequence[Label],
    *,
    catalog: Catalog | None = None,
    pins: VersionPins | None = None,
    seed: int = 0,
    n_folds: int = N_FOLDS,
    cal_folds: int = CAL_FOLDS,
    mode: Literal["cold", "warm"] | None = None,
) -> Calibrator:
    """Fit cold or warm per §9.4, choose isotonic or Venn-Abers per unit on out-of-fold predictions.

    ``n_folds`` call folds give the held-out predictions (metrics, the Venn-Abers choice, promotion);
    ``cal_folds`` is the number of cross-fitted booster / map pairs per warm unit.
    """
    names = feature_names(catalog or load_catalog())
    if not labels:
        return Calibrator.identity(catalog)
    data = LabelSet.from_labels(labels, names)
    if mode is None:
        mode = "warm" if (len(data) >= WARM_MIN_LABELS and data.n_calls >= WARM_MIN_CALLS) else "cold"
    global_fill = component_means(data.X, names)
    units, routing = fit_units(data, mode, global_fill=global_fill, seed=seed, cal_folds=cal_folds)

    # Out-of-fold predictions for both maps, folds split by call.
    oof = {"isotonic": np.full(len(data), np.nan), "venn_abers": np.full(len(data), np.nan)}
    for train, test in call_folds(data.calls, n_folds):
        sub_units, sub_routing = fit_units(data.take(train), mode, global_fill=global_fill, seed=seed, cal_folds=cal_folds)
        test_data = data.take(test)
        for key, rows in _route(sub_units, sub_routing, test_data).items():
            unit = sub_units.get(key) or sub_units["pooled"]
            for kind in ("isotonic", "venn_abers"):
                oof[kind][test[rows]] = unit.predict(test_data.X[rows], names, kind)[1]  # type: ignore[arg-type]
    have_oof = bool(np.all(np.isfinite(oof["isotonic"])))

    chosen = np.full(len(data), np.nan)
    for key, rows in _route(units, routing, data).items():
        unit = units.get(key) or units["pooled"]
        if have_oof and len(rows):
            iso_m = calibration_metrics(oof["isotonic"][rows], data.y[rows])
            va_m = calibration_metrics(oof["venn_abers"][rows], data.y[rows])
            unit.chosen = choose_map(iso_m, va_m)
            unit.heldout = {
                "isotonic": {k: iso_m[k] for k in ("n", "ece", "brier", "auroc")},
                "venn_abers": {k: va_m[k] for k in ("n", "ece", "brier", "auroc")},
                "chosen": unit.chosen,
            }
            chosen[rows] = oof[unit.chosen][rows]
    cal = Calibrator(
        version=_version(mode, labels),
        mode=mode,
        names=names,
        units=units,
        routing=routing,
        global_fill=global_fill,
        n_labels=len(data),
        n_calls=data.n_calls,
        trained_on=_trained_on(pins),
    )
    if have_oof:
        cal.oof = chosen
        groups = data.groups
        cal.metrics = {
            "heldout": True,
            "overall": calibration_metrics(chosen, data.y),
            "groups": {str(g): calibration_metrics(chosen[groups == g], data.y[groups == g]) for g in set(groups.tolist())},
        }
    else:
        _, p = cal.predict([(lb.features, lb.slot_group, lb.family) for lb in labels])
        cal.metrics = {"heldout": False, "overall": calibration_metrics(p, data.y)}  # too few calls to hold any out
    return cal


# --------------------------------------------------------------------------- retraining and promotion


def retrain_due(
    incumbent: Calibrator | None, n_labels: int, pins: VersionPins | None = None, *, every: int = RETRAIN_EVERY
) -> tuple[bool, str]:
    """§9.4: every 200 new labels, or any change to the extraction model, a prompt version or the ASR."""
    if incumbent is None or incumbent.mode == "identity":
        return (n_labels > 0, "no trained calibrator" if n_labels > 0 else "no labels yet")
    if n_labels - incumbent.n_labels >= every:
        return True, f"{n_labels - incumbent.n_labels} new labels"
    now, was = _trained_on(pins), incumbent.trained_on
    if pins is not None and was:
        if now["reasoning_model"] != was.get("reasoning_model"):
            return True, "extraction model changed"
        if now["prompt_versions"] != was.get("prompt_versions", {}):
            return True, "prompt version changed"
        if (now["asr_provider"], now["asr_model_version"]) != (was.get("asr_provider"), was.get("asr_model_version")):
            return True, "ASR changed"
    return False, "not due"


def should_promote(candidate_ece: float, incumbent_ece: float | None, *, tol: float = PROMOTION_TOL) -> tuple[bool, str]:
    """Promote unless held-out ECE worsens by more than ``tol``."""
    if incumbent_ece is None or not math.isfinite(incumbent_ece):
        return True, "no incumbent to compare with"
    if not math.isfinite(candidate_ece):
        return False, "candidate has no held-out ECE"
    if candidate_ece <= incumbent_ece + tol:
        return True, f"held-out ECE {candidate_ece:.4f} vs incumbent {incumbent_ece:.4f}"
    return False, f"held-out ECE worsens from {incumbent_ece:.4f} to {candidate_ece:.4f} (more than {tol})"


@dataclass
class RetrainReport:
    trigger: str
    candidate: Calibrator | None = None
    promoted: bool = False
    reason: str = ""
    candidate_ece: float | None = None
    incumbent_ece: float | None = None
    compared_on: int = 0  # labels both calibrators were scored on


def retrain(
    labels: Sequence[Label],
    *,
    incumbent: Calibrator | None = None,
    pins: VersionPins | None = None,
    catalog: Catalog | None = None,
    seed: int = 0,
    force: bool = False,
    n_folds: int = N_FOLDS,
    cal_folds: int = CAL_FOLDS,
) -> RetrainReport:
    """The nightly ``retrain_calibrator`` step (§11.4): fit when due, compare held-out ECE, promote or keep.

    Labels are in arrival order. The comparison uses the labels the incumbent never saw when there are at
    least 50 of them (else all labels): the candidate's out-of-fold predictions against the incumbent's.
    """
    due, trigger = retrain_due(incumbent, len(labels), pins)
    if not (due or force):
        return RetrainReport(trigger=trigger, reason="not due")
    cand = fit_calibrator(labels, catalog=catalog, pins=pins, seed=seed, n_folds=n_folds, cal_folds=cal_folds)
    report = RetrainReport(trigger=trigger if due else "forced", candidate=cand)
    y = np.asarray([lb.correct for lb in labels], dtype=float)
    start = (
        incumbent.n_labels
        if (incumbent is not None and incumbent.mode != "identity" and len(labels) - incumbent.n_labels >= MIN_NEW_FOR_COMPARISON)
        else 0
    )
    idx = np.arange(start, len(labels))
    report.compared_on = len(idx)
    cand_p = cand.oof if cand.oof is not None else cand.predict([(lb.features, lb.slot_group, lb.family) for lb in labels])[1]
    report.candidate_ece = ece(cand_p[idx], y[idx])
    if incumbent is not None:
        _, inc_p = incumbent.predict([(labels[i].features, labels[i].slot_group, labels[i].family) for i in idx])
        report.incumbent_ece = ece(inc_p, y[idx])
    report.promoted, report.reason = should_promote(report.candidate_ece, report.incumbent_ece)
    return report
