"""The calibration feature vector, one row per fact (spec §9.2).

``features(fact, transcript, profile)`` returns every feature of the §9.2 table, plus three the spec
adds elsewhere (``minicheck_score`` §9.1a, ``lexicon_near_miss`` §8.3, ``call_quality_capped`` §5.5).
Missing values are NaN, by these rules:

- ``entail_score`` / ``minicheck_score``: NaN when the check did not run (or had nothing to check);
- ``consistency``: NaN for tier C (§9.1b), and when no probe answered;
- ``self_score``: NaN when the extractor gave none (derived and seeded facts);
- ASR, diarization and role confidences: NaN when the fact has no evidence in the transcript;
- ``resolution_conf``: NaN without discourse output; 1.0 when the evidence needed no reference resolved;
- ``crm_link_score``: NaN when the fact is about no registry entity; 0 when the entity has no CRM match;
- ``grammar_readings`` / ``grammar_top_margin``: NaN when no amount, age or product span overlaps the evidence;
- ``second_pass_agreement``: NaN when the second pass did not run on those spans;
- ``distance_from_prior``: NaN when there is no prior FNA value (or no value now);
- ``aligner_score``: NaN when no evidence carries an alignment score;
- ``top_weight``: NaN when the fact has no hypotheses.

Counts and flags are never NaN. Status, slot policy and slot group are one-hot encoded. The gradient
boosting calibrator takes NaN natively; the cold-start score replaces it with group means (§9.4).
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Iterable, Mapping

import numpy as np

from .. import stats
from ..models.entities import DiscourseOutput
from ..models.facts import Evidence, Fact
from ..models.profile import Profile
from ..models.transcript import NumericSpan, Segment, Transcript, Word
from ..schema.slots import Catalog, load_catalog

NAN = float("nan")
LOW_ASR = 0.6
MARGIN_CAP = 100.0  # grammar_top_margin is the top-two score ratio, capped (one reading = the cap)
SECOND_PASS_TOL = 0.02
STATUS_VALUES = ("stated", "implied", "explicitly_unknown", "not_discussed", "contradicted")
POLICY_VALUES = ("categorical", "span_normalise", "open_list", "boolean", "text")

# The §9.2 table, in order; one-hots are expanded per catalog in ``feature_names``.
SPEC_FEATURES: tuple[str, ...] = (
    "self_score",
    "entail_score",
    "consistency",
    "n_evidence",
    "n_supporting_segments",
    "asr_mean_conf",
    "asr_min_conf",
    "low_asr_overlap",
    "diarization_conf",
    "role_conf",
    "speaker_is_client",
    "resolution_conf",
    "crm_link_score",
    "hedge_strength_max",
    "scale_ambiguous",
    "revised",
    "restated",
    "n_hypotheses",
    "top_weight",
    "lexicon_miss",
    "constraint_soft_violations",
    "derived",
    "grammar_readings",
    "grammar_top_margin",
    "second_pass_run",
    "second_pass_agreement",
    "distance_from_prior",
    "aligner_score",
    "reopened_by_coverage",
    "field_retries",
    "term_unresolved",
)
EXTRA_FEATURES: tuple[str, ...] = ("minicheck_score", "lexicon_near_miss", "call_quality_capped")
ONE_HOT_PREFIXES = ("status_", "policy_", "group_")


@lru_cache(maxsize=8)
def _names(groups: tuple[str, ...]) -> tuple[str, ...]:
    return (
        SPEC_FEATURES
        + EXTRA_FEATURES
        + tuple(f"status_{s}" for s in STATUS_VALUES)
        + tuple(f"policy_{p}" for p in POLICY_VALUES)
        + tuple(f"group_{g}" for g in groups)
    )


def feature_names(catalog: Catalog | None = None) -> list[str]:
    """Every feature, in a fixed order: the §9.2 table, the extras, then the one-hots."""
    catalog = catalog or load_catalog()
    return list(_names(tuple(catalog.group_names())))


# --------------------------------------------------------------------------- evidence words and spans


def word_offsets(seg: Segment) -> list[tuple[int, int]]:
    """Character offsets of each word in ``seg.text``; proportional when the words do not match the text."""
    text, low = seg.text, seg.text.lower()
    pos, out = 0, []
    for w in seg.words:
        t = w.text.strip()
        i = text.find(t, pos) if t else pos
        if i < 0:
            i = low.find(t.lower(), pos)
        if i < 0:
            n, length = max(1, len(seg.words)), len(text)
            return [(int(k * length / n), int((k + 1) * length / n)) for k in range(len(seg.words))]
        out.append((i, i + len(t)))
        pos = i + len(t)
    return out


class EvidenceIndex:
    """Word offsets and numeric spans per segment, computed once per transcript."""

    def __init__(self, transcript: Transcript) -> None:
        self.transcript = transcript
        self.segments = transcript.index()
        self._offsets: dict[str, list[tuple[int, int]]] = {}
        self.spans: dict[str, list[NumericSpan]] = {}
        for sp in transcript.numeric_spans:
            self.spans.setdefault(sp.segment_id, []).append(sp)

    def word_indices(self, ev: Evidence) -> list[int]:
        seg = self.segments.get(ev.segment_id)
        if seg is None:
            return []
        if ev.segment_id not in self._offsets:
            self._offsets[ev.segment_id] = word_offsets(seg)
        return [i for i, (s, e) in enumerate(self._offsets[ev.segment_id]) if s < ev.char_end and e > ev.char_start]

    def words(self, ev: Evidence) -> list[Word]:
        seg = self.segments.get(ev.segment_id)
        return [seg.words[i] for i in self.word_indices(ev)] if seg else []

    def overlapping_spans(self, evidence: Iterable[Evidence]) -> list[NumericSpan]:
        out: dict[str, NumericSpan] = {}
        for ev in evidence:
            idx = set(self.word_indices(ev))
            for sp in self.spans.get(ev.segment_id, []):
                if idx & set(range(sp.word_start, sp.word_end)):
                    out[sp.span_id] = sp
        return list(out.values())


def primary_evidence(fact: Fact) -> list[Evidence]:
    main = [e for e in fact.evidence if e.role in ("supports", "revises")]
    return main or [e for e in fact.evidence if e.role != "context"] or list(fact.evidence)


# --------------------------------------------------------------------------- normalisation records


def _walk(node: Any) -> Iterable[tuple[str | None, Any]]:
    if isinstance(node, Mapping):
        for k, v in node.items():
            yield str(k), v
            yield from _walk(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield None, v
            yield from _walk(v)


def normalization_flag(fact: Fact, name: str) -> bool:
    """True when the normalisation record carries ``name`` as a truthy key, a flag string, or a lexicon match."""
    if not fact.normalization:
        return False
    match = {"lexicon_miss": "miss", "lexicon_near_miss": "near_miss"}.get(name)
    for k, v in _walk(fact.normalization):
        if k == name and bool(v):
            return True
        if isinstance(v, str) and v == name:
            return True
        if match and k == "match" and v == match:
            return True
    return False


# --------------------------------------------------------------------------- the second pass


def _chosen_number(fact: Fact) -> float | None:
    if not fact.hypotheses:
        return None
    top = max(fact.hypotheses, key=lambda h: h.weight)
    if top.value is None:
        return None
    try:
        return stats.median(top.value.dist)
    except (TypeError, ValueError):
        return None


def _same_reading(a: float, b: float) -> bool:
    for x in (b, b * 12, b / 12):
        if abs(a - x) <= SECOND_PASS_TOL * max(abs(a), abs(x), 1e-9):
            return True
    return False


def second_pass(fact: Fact, spans: Iterable[NumericSpan]) -> tuple[bool, bool | None]:
    """(ran, agrees with the chosen reading) over the spans the second pass re-decoded (§5.6)."""
    ran = [sp for sp in spans if sp.second_pass_reading is not None]
    if not ran:
        return False, None
    chosen = _chosen_number(fact)
    agree = True
    for sp in ran:
        val = sp.second_pass_reading.value  # type: ignore[union-attr]
        if chosen is not None and isinstance(val, (int, float)):
            agree &= _same_reading(chosen, float(val))
        elif sp.second_pass_agreement is not None:
            agree &= bool(sp.second_pass_agreement)
    return True, agree


# --------------------------------------------------------------------------- the vector


def _num(v: Any) -> float:
    if v is None:
        return NAN
    try:
        x = float(v)
    except (TypeError, ValueError):
        return NAN
    return x


def _distance_from_prior(fact: Fact) -> float:
    if fact.prior_value is None or fact.value is None:
        return NAN
    a, b = fact.value.dist, fact.prior_value.dist
    try:
        if stats.is_boolean(a) or stats.is_boolean(b):
            return 0.0 if (stats.p_true(a) >= 0.5) == (stats.p_true(b) >= 0.5) else 1.0
        if stats.is_categorical(a) or stats.is_categorical(b):
            return 0.0 if stats.mode_label(a).strip().lower() == stats.mode_label(b).strip().lower() else 1.0
        cur, prior = stats.median(a), stats.median(b)
    except (TypeError, ValueError):
        return NAN
    if fact.value.unit == "date":
        return abs(cur - prior)  # years apart
    return abs(cur - prior) / max(abs(prior), 1e-9)


def _entity_for(fact: Fact, profile: Profile) -> str | None:
    if fact.instance_key in ("client", "spouse"):
        return "person.client_primary" if fact.instance_key == "client" else "person.spouse"
    if fact.instance_key and fact.instance_key in profile.entities:
        return fact.instance_key
    return next((r for r in fact.entity_refs if r in profile.entities), None)


def _resolution_conf(fact: Fact, evidence: list[Evidence], discourse: DiscourseOutput | None, entity: str | None) -> float:
    """Min confidence over the mentions the fact relies on: those in its evidence segments that resolve to its
    entity or stay unresolved ("It's about two fifty" — the pronoun sits outside the quoted span)."""
    if discourse is None:
        return NAN
    segs = {e.segment_id for e in evidence}
    mine = {r for r in (entity, fact.instance_key, *fact.entity_refs) if r}
    confs = [m.resolution_conf for m in discourse.mentions if m.segment_id in segs and (m.entity_id is None or m.entity_id in mine)]
    return min(confs) if confs else 1.0


def features(
    fact: Fact,
    transcript: Transcript,
    profile: Profile,
    *,
    catalog: Catalog | None = None,
    discourse: DiscourseOutput | None = None,
    verification: Mapping[str, Any] | None = None,
    index: EvidenceIndex | None = None,
) -> dict[str, float]:
    """The complete §9.2 vector for one fact, as floats with NaN for missing values."""
    catalog = catalog or load_catalog(profile.schema_version)
    slot = catalog.slot(fact.slot_id)
    ver = verification if verification is not None else fact.verification
    index = index or EvidenceIndex(transcript)
    evidence = primary_evidence(fact)
    segs = [index.segments[sid] for sid in dict.fromkeys(e.segment_id for e in evidence) if sid in index.segments]
    words = [w for ev in evidence for w in index.words(ev)]
    if words:
        confs = [w.conf for w in words]
        asr_mean, asr_min, low = float(np.mean(confs)), float(min(confs)), sum(c < LOW_ASR for c in confs) / len(confs)
    elif evidence:
        confs = [e.asr_mean_conf for e in evidence]
        asr_mean, asr_min, low = float(np.mean(confs)), float(min(confs)), sum(c < LOW_ASR for c in confs) / len(confs)
    else:
        asr_mean = asr_min = low = NAN
    spans = index.overlapping_spans(evidence)
    ran, agrees = second_pass(fact, spans)
    margins = [min(sp.top_margin, MARGIN_CAP) for sp in spans]
    entity = _entity_for(fact, profile)
    crm = NAN
    if entity is not None:
        matches = profile.entities[entity].crm_match
        crm = float(matches[0][1]) if matches else 0.0
    supports = {e.segment_id for e in fact.evidence if e.role == "supports"}
    top_w = max((h.weight for h in fact.hypotheses), default=NAN)
    term = fact.term_resolution or {}
    term_unresolved = bool(term) and (term.get("status") == "unresolved" or term.get("unresolved") is True or not term.get("candidate"))
    quality = transcript.quality
    attribution = {"client": 1.0, "advisor_confirmed_by_client": 1.0, "mixed": 0.5, "advisor_suggested": 0.0}[fact.speaker_attribution]
    aligner = [e.aligner_score for e in fact.evidence if e.aligner_score is not None]
    out: dict[str, float] = {
        "self_score": _num(fact.self_score),
        "entail_score": _num(ver.get("entail_score")),
        "consistency": NAN if slot.sensitivity_tier == "C" else _num(ver.get("consistency")),
        "n_evidence": float(len(fact.evidence)),
        "n_supporting_segments": float(len({e.segment_id for e in fact.evidence if e.role in ("supports", "revises")})),
        "asr_mean_conf": asr_mean,
        "asr_min_conf": asr_min,
        "low_asr_overlap": low,
        "diarization_conf": min((s.diarization_conf for s in segs), default=NAN),
        "role_conf": min((s.role_conf for s in segs), default=NAN),
        "speaker_is_client": attribution,
        "resolution_conf": _resolution_conf(fact, evidence, discourse, entity),
        "crm_link_score": crm,
        "hedge_strength_max": max((h.strength for h in fact.hedges), default=0.0),
        "scale_ambiguous": float(normalization_flag(fact, "scale_ambiguous")),
        "revised": float(any(e.role == "revises" for e in fact.evidence) or any(h.revises for h in fact.hypotheses)),
        "restated": float(len(supports) >= 2 and (top_w if not math.isnan(top_w) else 0.0) >= 0.9),
        "n_hypotheses": float(len(fact.hypotheses)),
        "top_weight": top_w,
        "lexicon_miss": float(normalization_flag(fact, "lexicon_miss")),
        "constraint_soft_violations": float(ver.get("constraint_soft_violations") or 0),
        "derived": float(fact.derivation is not None or fact.source == "derived"),
        "grammar_readings": float(max(len(sp.readings) for sp in spans)) if spans else NAN,
        "grammar_top_margin": float(min(margins)) if margins else NAN,
        "second_pass_run": float(ran),
        "second_pass_agreement": NAN if agrees is None else float(agrees),
        "distance_from_prior": _distance_from_prior(fact),
        "aligner_score": float(max(aligner)) if aligner else NAN,
        "reopened_by_coverage": float(fact.reopened_by_coverage),
        "field_retries": float(fact.field_retries),
        "term_unresolved": float(term_unresolved),
        "minicheck_score": _num(ver.get("minicheck_score")),
        "lexicon_near_miss": float(normalization_flag(fact, "lexicon_near_miss")),
        "call_quality_capped": float(bool(quality and quality.cap_p_correct)),
    }
    for s in STATUS_VALUES:
        out[f"status_{s}"] = float(fact.status == s)
    for p in POLICY_VALUES:
        out[f"policy_{p}"] = float(slot.policy == p)
    for g in catalog.group_names():
        out[f"group_{g}"] = float(slot.group == g)
    return out


def vector(feats: Mapping[str, float | None], names: list[str]) -> np.ndarray:
    """A feature dict (NaN or None for missing) as an array in ``names`` order."""
    return np.asarray([NAN if feats.get(n) is None else float(feats[n]) for n in names], dtype=float)  # type: ignore[arg-type]


def to_storable(feats: Mapping[str, float]) -> dict[str, float | None]:
    """NaN → None, so ``Fact.features`` survives a JSON round trip (Postgres jsonb, Temporal payloads)."""
    return {k: (None if isinstance(v, float) and math.isnan(v) else float(v)) for k, v in feats.items()}
