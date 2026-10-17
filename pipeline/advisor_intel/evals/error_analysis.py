"""Weekly error analysis (spec §15.5).

Errors come from two places: the ``production-errors`` dataset (reason-coded rejections and missed slots from
review, §11.4) and the latest gold experiment (per-fact differences from gold). To find patterns before sampling,
error quotes and reviewer notes are embedded with the §6.5 segment embedder and clustered with HDBSCAN; each cluster
of five or more errors gets a one-line label from the small model and becomes a candidate extraction hint, lexicon
entry or grammar rule. Each week 20 errors per tier A slot group are sampled into a sheet for a person to label
with a cause code (ASR, diarization, coreference, extraction miss, wrong scale, lexicon, attribution, derivation;
a heuristic suggests one), and the top two causes feed the next sprint.

CLI::

    python -m advisor_intel.evals.error_analysis weekly [--days 7] [--rows ROWS.jsonl | --experiment NAME] [--out DIR] [--no-label]
    python -m advisor_intel.evals.error_analysis causes SHEET.jsonl [--k 2]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, get_args

import numpy as np
from pydantic import BaseModel, Field

from ..embeddings import Embedder, get_embedder
from ..models.runs import VersionPins
from ..schema.slots import Catalog, SlotDef, fact_id_for, load_catalog
from ._runtime import data_dir, run_sync, unit_hash, write_json
from .datasets import EVAL_PROJECT, PRODUCTION_ERRORS, DatasetRecord, DatasetStore, get_store
from .gold import GoldFact
from .records import EvalRecord, SystemFact
from .scorers import _overlaps, align, covers, records_from_rows, value_text

Cause = Literal["asr", "diarization", "coreference", "extraction_miss", "wrong_scale", "lexicon", "attribution", "derivation"]
CAUSES: tuple[str, ...] = get_args(Cause)
Suggestion = Literal["extraction_hint", "lexicon_entry", "grammar_rule", "other"]

SAMPLE_PER_GROUP = 20  # §15.5: errors sampled per tier A slot group each week
MIN_CLUSTER = 5  # §15.5: clusters of five or more errors get a label
TOP_CAUSES = 2  # §15.5: the top two causes feed the next sprint


# --------------------------------------------------------------------------- errors


class ErrorItem(BaseModel):
    """One error to cluster, sample and label."""

    id: str
    source: Literal["production", "gold"]
    call_id: str = ""
    fact_id: str = ""
    slot_id: str
    group: str
    tier: str = "C"
    kind: str  # rejected, missed, invented, wrong_status, wrong_value, attribution, ungrounded
    reason: str  # the reviewer's reason code, or what the comparison with gold found
    detail: str = ""  # "extracted 50, gold 50,000"
    quotes: list[str] = []
    said: str = ""
    note: str = ""  # the reviewer's note
    at: str | None = None
    features: dict[str, float | None] = {}
    cluster: int | None = None
    suggested_cause: Cause | None = None
    cause: Cause | None = None  # set by the person labelling the weekly sample

    @property
    def text(self) -> str:
        """What is embedded: the error's quotes, the client's words and the reviewer's note (§15.5)."""
        parts = [*(f'"{q}"' for q in self.quotes[:4] if q), self.said, self.note, self.detail]
        return " · ".join(p for p in parts if p) or f"{self.kind} {self.slot_id}"


def _slot(catalog: Catalog, slot_id: str) -> SlotDef | None:
    for candidate in (slot_id, re.sub(r"\[[^\]]*\]", "[]", slot_id)):
        try:
            return catalog.slot(candidate)
        except KeyError:
            continue
    return None


def _features(raw: Mapping[str, Any] | None) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for k, v in (raw or {}).items():
        if isinstance(v, bool):
            out[k] = float(v)
        elif isinstance(v, (int, float)):
            out[k] = None if math.isnan(float(v)) else float(v)
    return out


def errors_from_dataset(rows: Iterable[DatasetRecord], *, catalog: Catalog | None = None, since: datetime | None = None) -> list[ErrorItem]:
    """``production-errors`` rows (written by ``datasets.sync_error_dataset``) as errors; ``since`` keeps recent ones."""
    catalog = catalog or load_catalog()
    out = []
    for r in rows:
        inp, exp, meta = r.input or {}, r.expected or {}, r.metadata or {}
        at = meta.get("at")
        if since is not None and at and datetime.fromisoformat(str(at)).astimezone(UTC) < since:
            continue
        slot_id = str(meta.get("slot_id") or inp.get("slot_id") or "")
        slot = _slot(catalog, slot_id) if slot_id else None
        extracted = inp.get("extracted") or ""
        out.append(
            ErrorItem(
                id=f"production:{r.id}",
                source="production",
                call_id=str(inp.get("call_id") or inp.get("run_id") or ""),
                fact_id=str(inp.get("fact_id") or ""),
                slot_id=slot.slot_id if slot else slot_id,
                group=str(meta.get("group") or (slot.group if slot else "unknown")),
                tier=str(meta.get("tier") or (slot.sensitivity_tier if slot else "C")),
                kind=str(meta.get("kind") or exp.get("action") or "rejected"),
                reason=str(meta.get("reason") or exp.get("reason") or "other"),
                detail=f"extracted {extracted}" if extracted else "",
                quotes=[str(q) for q in inp.get("quotes") or [] if q],
                said=str(inp.get("said") or ""),
                note=str(exp.get("note") or ""),
                at=str(at) if at else None,
                features=_features(meta.get("features")),
            )
        )
    return out


def _p50(v: Any) -> float | None:
    p = getattr(v, "p50", None)
    return float(p) if isinstance(p, (int, float)) and not isinstance(p, bool) else None


def scale_mismatch(system_value: Any, gold_value: Any) -> bool:
    """The two medians differ by a power of ten (×10, ×1,000, ×1,000,000…): a misread scale word (§7.4)."""
    s, g = _p50(system_value), _p50(gold_value)
    if s is None or g is None or s <= 0 or g <= 0:
        return False
    k = math.log10(s / g)
    return abs(k) >= 0.95 and abs(k - round(k)) < 0.05


def _coarse(attribution: str | None) -> str:
    return "advisor" if attribution == "advisor_suggested" else "client"


def errors_from_records(records: Sequence[EvalRecord], *, catalog: Catalog | None = None) -> list[ErrorItem]:
    """Every fact where a gold experiment's output differs from gold, as errors (§15.5).

    Kinds: ``missed`` (a stated or implied gold fact not found), ``invented`` (found but not discussed),
    ``wrong_status``, ``wrong_value`` (gold p50 outside the system p10–p90), ``attribution`` (client vs advisor) and
    ``ungrounded`` (evidence that misses every gold span).
    """
    out: list[ErrorItem] = []
    for rec in records:
        al = align(rec.system, rec.gold, catalog=catalog)
        for p in al.pairs:
            s: SystemFact | None = p.system
            g: GoldFact | None = p.gold
            sys_quotes = [x.quote for x in (s.spans if s else []) if x.quote]
            gold_quotes = [x.quote for x in (g.spans if g else []) if x.quote]
            feats = dict(s.features) if s else {}
            if s is not None and s.vague:
                feats["vague"] = 1.0
            base = {
                "source": "gold",
                "call_id": rec.gold.call_id,
                "fact_id": fact_id_for(p.slot.slot_id, p.key[1]),
                "slot_id": p.slot.slot_id,
                "group": p.group,
                "tier": p.tier,
                "note": g.note if g else "",
            }
            found: list[tuple[str, str, str, list[str]]] = []  # (kind, reason, detail, quotes)
            if p.gold_valued and not p.system_found:
                reason = "missed_implied" if g is not None and g.status == "implied" else "missed_slot"
                found.append(("missed", reason, f"missed {p.label} (gold {p.gold_status})", gold_quotes))
            elif g is None:
                if s is not None and s.extracted and s.found:
                    found.append(("invented", "invented", f"invented {p.label} (marked {s.status}; not discussed)", sys_quotes))
            elif s is not None and p.system_status != p.gold_status:
                found.append(
                    (
                        "wrong_status",
                        f"{p.system_status}_for_{p.gold_status}",
                        f"marked {p.label} {p.system_status}, gold {p.gold_status}",
                        sys_quotes + gold_quotes,
                    )
                )
            elif p.gold_valued and s is not None:
                if g.value is not None and not covers(s.value, g.value):
                    if scale_mismatch(s.value, g.value):
                        feats["scale_mismatch"] = 1.0
                    found.append(
                        (
                            "wrong_value",
                            "wrong_value",
                            f"{p.label}: extracted {value_text(s.value)}, gold {value_text(g.value)}",
                            sys_quotes or gold_quotes,
                        )
                    )
                if _coarse(s.attribution) != _coarse(g.attribution):
                    reason = "advisor_as_client" if _coarse(g.attribution) == "advisor" else "client_as_advisor"
                    found.append(
                        (
                            "attribution",
                            reason,
                            f"{p.label}: attributed to {s.attribution}, gold {g.attribution}",
                            sys_quotes or gold_quotes,
                        )
                    )
                if s.extracted and s.spans and g.spans and not any(_overlaps(x, y) for x in s.spans for y in g.spans):
                    found.append(("ungrounded", "ungrounded", f"{p.label}: evidence misses the gold span", sys_quotes))
            for kind, reason, detail, quotes in found:
                out.append(
                    ErrorItem(
                        id=f"gold:{rec.gold.call_id}:{base['fact_id']}:{kind}",
                        kind=kind,
                        reason=reason,
                        detail=detail,
                        quotes=quotes,
                        features=_features(feats),
                        **base,
                    )
                )
    return out


def experiment_records(experiment: str, *, project: str | None = None, init: Callable[..., Any] | None = None) -> list[EvalRecord]:
    """The scored rows of a Braintrust experiment (needs ``BRAINTRUST_API_KEY``): root spans with output and expected."""
    if init is None:
        import braintrust

        init = braintrust.init
    exp = init(project=project or EVAL_PROJECT, experiment=experiment, open=True)
    return records_from_rows(r for r in exp.fetch() if not r.get("span_parents"))


# --------------------------------------------------------------------------- cause codes


def _feat(e: ErrorItem, name: str, default: float) -> float:
    v = e.features.get(name)
    return default if v is None or math.isnan(v) else float(v)


def suggest_cause(e: ErrorItem) -> Cause:
    """A first guess at the cause from the reason code and the §9.2 features; the person labelling decides."""
    reason = e.reason
    if reason == "misheard" or _feat(e, "low_asr_overlap", 0.0) >= 0.3 or _feat(e, "asr_min_conf", 1.0) < 0.5:
        return "asr"
    if reason == "wrong_person":
        return "diarization" if min(_feat(e, "diarization_conf", 1.0), _feat(e, "role_conf", 1.0)) < 0.7 else "coreference"
    if reason == "wrong_entity" or _feat(e, "resolution_conf", 1.0) < 0.6:
        return "coreference"
    if reason in ("advisor_said_it", "advisor_as_client", "client_as_advisor") or e.kind == "attribution":
        return "attribution"
    if _feat(e, "scale_mismatch", 0.0) >= 0.5 or _feat(e, "scale_ambiguous", 0.0) >= 0.5:
        return "wrong_scale"
    if _feat(e, "lexicon_miss", 0.0) >= 0.5 or _feat(e, "term_unresolved", 0.0) >= 0.5:
        return "lexicon"
    if (reason == "not_what_they_meant" or e.kind == "wrong_value") and (
        _feat(e, "hedge_strength_max", 0.0) > 0 or _feat(e, "vague", 0.0) >= 0.5
    ):
        return "lexicon"
    if _feat(e, "derived", 0.0) >= 0.5 or "implied" in reason:
        return "derivation"
    return "extraction_miss"


# --------------------------------------------------------------------------- clustering


class ClusterLabel(BaseModel):
    """The small model's label for one cluster (every field required, for strict structured output)."""

    label: str = Field(description="One line, at most 15 words, naming the pattern the errors share")
    cause: Cause = Field(description="The most likely cause code for the cluster")
    suggestion: Suggestion = Field(description="Which fix the cluster is a candidate for")
    proposal: str = Field(description="The concrete extraction hint, lexicon entry or grammar rule to try, in one or two sentences")


class ErrorCluster(BaseModel):
    cluster_id: int
    size: int
    error_ids: list[str]
    exemplars: list[str]  # texts of the members nearest the cluster's centre
    slots: dict[str, int]
    reasons: dict[str, int]
    groups: dict[str, int]
    sources: dict[str, int]
    label: ClusterLabel | None = None


def embed_errors(errors: Sequence[ErrorItem], embedder: Embedder | None = None) -> np.ndarray:
    """Unit vectors of the error texts, from the §6.5 segment embedder."""
    vecs = np.asarray((embedder or get_embedder()).embed_documents([e.text for e in errors]), dtype=float)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.where(norms == 0, 1.0, norms)


def cluster_errors(
    errors: Sequence[ErrorItem],
    *,
    embedder: Embedder | None = None,
    vectors: np.ndarray | None = None,
    min_cluster_size: int = MIN_CLUSTER,
    min_samples: int | None = None,
    n_exemplars: int = 3,
) -> list[ErrorCluster]:
    """HDBSCAN over the error embeddings (largest cluster first). Sets each error's ``cluster``; noise stays None."""
    from sklearn.cluster import HDBSCAN

    for e in errors:
        e.cluster = None
    if len(errors) < max(min_cluster_size, 2):
        return []
    x = vectors if vectors is not None else embed_errors(errors, embedder)
    labels = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples, metric="euclidean", copy=True).fit_predict(x)
    clusters = []
    for k in sorted({int(v) for v in labels if v >= 0}):
        idx = np.flatnonzero(labels == k)
        centre = x[idx].mean(axis=0)
        order = idx[np.argsort(np.linalg.norm(x[idx] - centre, axis=1), kind="stable")]
        members = [errors[i] for i in idx]
        clusters.append(
            ErrorCluster(
                cluster_id=k,
                size=len(idx),
                error_ids=[m.id for m in members],
                exemplars=[errors[i].text for i in order[:n_exemplars]],
                slots=dict(Counter(m.slot_id for m in members).most_common()),
                reasons=dict(Counter(m.reason for m in members).most_common()),
                groups=dict(Counter(m.group for m in members).most_common()),
                sources=dict(Counter(m.source for m in members).most_common()),
            )
        )
    clusters.sort(key=lambda c: (-c.size, c.cluster_id))
    by_id = {e.id: e for e in errors}
    for c in clusters:
        for eid in c.error_ids:
            by_id[eid].cluster = c.cluster_id
    return clusters


LABEL_INSTRUCTIONS = """\
You review errors made by a pipeline that turns recorded financial-advice calls into a client profile (South Africa;
amounts in rand, often spoken loosely, sometimes in Afrikaans or slang). The errors you are shown were clustered
together because their quotes and reviewer notes are similar. Name the pattern they share in one line of at most
15 words, pick the most likely cause, and say which fix the cluster is a candidate for:

- extraction_hint: an instruction for the extraction prompt of a slot group (what to look for, how to read it);
- lexicon_entry: a vague phrase, slang amount or product term and what it means (for the vagueness lexicon);
- grammar_rule: how spoken numbers, scale words or currency are parsed (for the ASR number grammar);
- other: anything else (diarization, coreference, review UI).

Causes: asr (misheard words), diarization (wrong speaker), coreference (wrong person or entity), extraction_miss (the
evidence was there and was missed or misread), wrong_scale (thousand vs million and similar), lexicon (a vague or
unusual expression mapped to the wrong value), attribution (advisor suggestion vs client statement), derivation (an
implied fact computed wrongly). Base the label only on the examples; do not invent details.
"""


def cluster_label_agent(pins: VersionPins | None = None) -> Any:
    """The labelling agent: the small model (§13.8) with ``NativeOutput(ClusterLabel)``."""
    from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext

    from ..llm import for_role

    rm = for_role("small", pins)
    agent = Agent(
        rm.model,
        name="error_cluster_labeller",
        output_type=NativeOutput(ClusterLabel),
        instructions=LABEL_INSTRUCTIONS,
        model_settings=rm.settings,
        retries=2,
    )

    @agent.output_validator
    def _one_line(ctx: RunContext[None], out: ClusterLabel) -> ClusterLabel:
        label = out.label.strip()
        if not label or "\n" in label or len(label) > 120:
            raise ModelRetry("The label must be one line of at most 120 characters (about 15 words).")
        return out.model_copy(update={"label": label})

    return agent


def cluster_prompt(cluster: ErrorCluster, errors: Mapping[str, ErrorItem], *, max_examples: int = 12) -> str:
    """The labelling prompt: counts by slot and reason, then member examples nearest the centre first."""
    lines = [
        f"Cluster of {cluster.size} errors.",
        "Slots: " + ", ".join(f"{k} ×{v}" for k, v in list(cluster.slots.items())[:8]),
        "Reasons: " + ", ".join(f"{k} ×{v}" for k, v in cluster.reasons.items()),
        "",
        "Examples:",
    ]
    order = {t: i for i, t in enumerate(cluster.exemplars)}
    members = sorted((errors[i] for i in cluster.error_ids if i in errors), key=lambda e: (order.get(e.text, len(order)), e.id))
    for e in members[:max_examples]:
        lines.append(f"- [{e.slot_id} · {e.kind} · {e.reason}] {e.text}")
    return "\n".join(lines)


def label_clusters(
    clusters: Sequence[ErrorCluster],
    errors: Sequence[ErrorItem],
    *,
    agent: Any = None,
    pins: VersionPins | None = None,
    min_size: int = MIN_CLUSTER,
    max_examples: int = 12,
) -> list[ErrorCluster]:
    """A one-line label from the small model for every cluster of ``min_size`` or more errors (§15.5)."""
    todo = [c for c in clusters if c.size >= min_size]
    if not todo:
        return list(clusters)
    agent = agent or cluster_label_agent(pins)
    by_id = {e.id: e for e in errors}
    labelled = {}
    for c in todo:
        result = run_sync(agent.run(cluster_prompt(c, by_id, max_examples=max_examples)))
        labelled[c.cluster_id] = c.model_copy(update={"label": result.output})
    return [labelled.get(c.cluster_id, c) for c in clusters]


# --------------------------------------------------------------------------- the weekly sample


def tier_a_groups(catalog: Catalog | None = None) -> list[str]:
    return sorted({s.group for s in (catalog or load_catalog()).tier_a()})


def weekly_sample(
    errors: Sequence[ErrorItem],
    *,
    per_group: int = SAMPLE_PER_GROUP,
    groups: Sequence[str] | None = None,
    catalog: Catalog | None = None,
    seed: str = "",
) -> list[ErrorItem]:
    """Up to ``per_group`` errors on tier A slots per slot group, each with a suggested cause code (§15.5).

    Deterministic for a seed (the week). Within a group, errors are drawn round-robin across (source, cluster)
    buckets, so one large cluster or one source does not fill the sample.
    """
    groups = list(groups) if groups is not None else tier_a_groups(catalog)
    out: list[ErrorItem] = []
    for g in groups:
        buckets: dict[tuple[str, int], list[ErrorItem]] = {}
        for e in errors:
            if e.group == g and e.tier == "A":
                buckets.setdefault((e.source, -1 if e.cluster is None else e.cluster), []).append(e)
        queues = [
            sorted(items, key=lambda e: unit_hash(f"{seed}|{e.id}"))
            for _, items in sorted(buckets.items(), key=lambda kv: unit_hash(f"{seed}|{g}|{kv[0][0]}|{kv[0][1]}"))
        ]
        picked: list[ErrorItem] = []
        while len(picked) < per_group and any(queues):
            for q in queues:
                if q and len(picked) < per_group:
                    picked.append(q.pop(0))
        out.extend(e.model_copy(update={"suggested_cause": e.suggested_cause or suggest_cause(e)}) for e in picked)
    return out


SHEET_FIELDS = ("id", "source", "call_id", "fact_id", "slot_id", "group", "kind", "reason", "detail", "quotes", "said", "note", "cluster")


def write_sheet(sample: Sequence[ErrorItem], path: Path, *, clusters: Sequence[ErrorCluster] = ()) -> Path:
    """The labelling sheet: one JSON line per sampled error with an empty ``cause`` for the person to fill."""
    labels = {c.cluster_id: c.label.label for c in clusters if c.label is not None}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for e in sample:
            row = {k: getattr(e, k) for k in SHEET_FIELDS}
            row.update(
                cluster_label=labels.get(e.cluster) if e.cluster is not None else None,
                suggested_cause=e.suggested_cause,
                cause=e.cause or "",
            )
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def read_sheet(path: Path) -> list[ErrorItem]:
    """A labelled sheet back as errors; a cause outside the eight codes is an error naming the row."""
    items, bad = [], []
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        cause = (row.get("cause") or "").strip().lower().replace(" ", "_") or None
        if cause is not None and cause not in CAUSES:
            bad.append(f"line {n} ({row.get('id')}): {row.get('cause')!r}")
            cause = None
        row.pop("cluster_label", None)
        items.append(ErrorItem.model_validate(dict(row, cause=cause, tier="A")))
    if bad:
        raise ValueError("unknown cause codes (use one of " + ", ".join(CAUSES) + "): " + "; ".join(bad))
    return items


def top_causes(items: Iterable[ErrorItem], k: int = TOP_CAUSES, *, include_suggested: bool = False) -> list[tuple[str, int]]:
    """The most frequent cause codes among labelled errors (the next sprint's focus, §15.5)."""
    counts = Counter(c for e in items if (c := e.cause or (e.suggested_cause if include_suggested else None)))
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:k]


# --------------------------------------------------------------------------- the weekly run


class WeeklyAnalysis(BaseModel):
    week: str
    n_errors: int
    n_production: int
    n_gold: int
    clusters: list[ErrorCluster]
    sample_size: int
    sample_by_group: dict[str, int]
    sheet: str
    suggested_top_causes: list[tuple[str, int]]

    def markdown(self) -> str:
        lines = [
            f"## Error analysis, week {self.week}",
            "",
            (
                f"{self.n_errors} errors ({self.n_production} from production review, {self.n_gold} from the gold experiment); "
                f"{len(self.clusters)} clusters; {self.sample_size} sampled for labelling in `{self.sheet}`."
            ),
            "",
            "Suggested top causes (until the sheet is labelled): "
            + (", ".join(f"{c} ({n})" for c, n in self.suggested_top_causes) or "none"),
        ]
        labelled = [c for c in self.clusters if c.label is not None]
        if labelled:
            lines += ["", "| cluster | errors | label | cause | candidate fix |", "|---:|---:|---|---|---|"]
            for c in labelled:
                assert c.label is not None
                lines.append(
                    f"| {c.cluster_id} | {c.size} | {c.label.label} | {c.label.cause} | {c.label.suggestion}: {c.label.proposal} |"
                )
        return "\n".join(lines)


def iso_week(at: datetime | None = None) -> str:
    y, w, _ = (at or datetime.now(UTC)).isocalendar()
    return f"{y}-W{w:02d}"


def run_weekly(
    *,
    store: DatasetStore | None = None,
    records: Sequence[EvalRecord] | None = None,
    out_dir: Path | None = None,
    days: int | None = 7,
    label: bool = True,
    agent: Any = None,
    pins: VersionPins | None = None,
    embedder: Embedder | None = None,
    catalog: Catalog | None = None,
    now: datetime | None = None,
) -> WeeklyAnalysis:
    """Gather the week's errors, cluster and label them, draw the sample and write the sheet and the cluster file."""
    catalog = catalog or load_catalog()
    now = now or datetime.now(UTC)
    week = iso_week(now)
    since = now - timedelta(days=days) if days else None
    production = errors_from_dataset((store or get_store()).fetch(PRODUCTION_ERRORS), catalog=catalog, since=since)
    gold = errors_from_records(records, catalog=catalog) if records else []
    errors = production + gold
    clusters = cluster_errors(errors, embedder=embedder)
    if label and clusters:
        clusters = label_clusters(clusters, errors, agent=agent, pins=pins)
    sample = weekly_sample(errors, catalog=catalog, seed=week)
    out = (out_dir or (data_dir() / "error-analysis")) / week
    sheet = write_sheet(sample, out / "sample.jsonl", clusters=clusters)
    analysis = WeeklyAnalysis(
        week=week,
        n_errors=len(errors),
        n_production=len(production),
        n_gold=len(gold),
        clusters=clusters,
        sample_size=len(sample),
        sample_by_group=dict(Counter(e.group for e in sample)),
        sheet=str(sheet),
        suggested_top_causes=top_causes(sample, include_suggested=True),
    )
    write_json(out / "clusters.json", analysis.model_dump(mode="json"))
    (out / "summary.md").write_text(analysis.markdown() + "\n")
    return analysis


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Weekly error analysis (spec §15.5).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("weekly", help="cluster and label the week's errors and write the labelling sheet")
    w.add_argument("--days", type=int, default=7, help="production errors from the last N days (0: all)")
    src = w.add_mutually_exclusive_group()
    src.add_argument("--rows", type=Path, default=None, help="scored rows of a local gold run (default: the latest gold-calls run)")
    src.add_argument("--experiment", default=None, help="a Braintrust gold experiment to read instead (needs BRAINTRUST_API_KEY)")
    w.add_argument("--out", type=Path, default=None)
    w.add_argument("--no-label", action="store_true", help="skip the small-model cluster labels")
    c = sub.add_parser("causes", help="the top causes in a labelled sheet")
    c.add_argument("sheet", type=Path)
    c.add_argument("--k", type=int, default=TOP_CAUSES)
    args = ap.parse_args(argv)
    if args.cmd == "causes":
        for cause, n in top_causes(read_sheet(args.sheet), args.k):
            print(f"{cause}\t{n}")
        return 0
    from .datasets import GOLD_CALLS
    from .extraction_eval import EVAL_NAME, load_rows, rows_path

    if args.experiment:
        try:
            records = experiment_records(args.experiment)
        except Exception as e:  # noqa: BLE001 - a missing or unreadable gold run still leaves the production errors
            print(
                f"could not read the gold experiment {args.experiment!r} ({type(e).__name__}: {e}): production errors only", file=sys.stderr
            )
            records = []
    else:
        path = args.rows or rows_path(EVAL_NAME, GOLD_CALLS)
        records = records_from_rows(load_rows(path)) if path.exists() else []
        if not path.exists():
            print(f"no gold run rows at {path}: production errors only", file=sys.stderr)
    analysis = run_weekly(records=records, out_dir=args.out, days=args.days or None, label=not args.no_label)
    print(analysis.markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
