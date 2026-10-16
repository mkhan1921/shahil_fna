"""The extraction experiment (spec §15.4).

``braintrust_eval()`` defines the Braintrust ``Eval``: data from a dataset (``synthetic-calls`` on pull requests,
``gold-calls`` nightly), task ``run_stages_3_to_6_from_cache`` — the same pipeline code as production, run in-process
on the cached Stage 1–2 output — the per-item §15.3 scorers, and the current version pins as experiment
metadata. The Python SDK has no summary-scorer hook, so a reporter computes the summary scores (ECE per slot group,
Brier, PoS error, auto-accept rate, …), writes the JSON report the CI gate reads, and — when a baseline is
configured — fails the run on a gated regression.

``run_local()`` runs the same task and scorers without Braintrust and writes the same report, for offline runs,
tests and the GEPA and model-swap loops. ``python -m advisor_intel.evals.extraction_eval`` is its CLI.

The pipeline runner is injected (``configure(runner=...)``); by default it is
``advisor_intel.pipeline.process_cached_call(call, pins)``, imported lazily.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import yaml
from pydantic import BaseModel

from ..config import PIPELINE_DIR, get_settings
from ..models.fna import FnaDocument
from ..models.runs import VersionPins
from ..models.transcript import Transcript
from ._runtime import braintrust_enabled, data_dir, lazy_attr, prompts_dir, run_sync, to_jsonable, write_json
from .datasets import EVAL_BASELINES, EVAL_PROJECT, CachedCall, DatasetRecord, DatasetStore, Stage12Builder, Stage12Cache, get_store
from .gold import GoldCall
from .records import EvalRecord, SystemCallOutput, summarise_result
from .scorers import ITEM_SCORE_NAMES, ITEM_SCORERS, ItemScorer, asr_metrics, item_metrics, summary_scores
from .synthetic import SYNTHETIC_DATASET

EVAL_NAME = "advisor-extraction"
Runner = Callable[[CachedCall, VersionPins], Any]
GATE_ENV = "ADVISOR_INTEL_EVAL_GATE"  # "1": the reporter gates the run against the baseline
BASELINE_ENV = "ADVISOR_INTEL_EVAL_BASELINE"  # a baseline report path, or "store:<ref>" for the eval-baselines dataset


# --------------------------------------------------------------------------- configuration


@dataclass
class EvalContext:
    """What the task needs besides the dataset row; set by ``configure`` (CLI, tests, GEPA, model swaps)."""

    runner: Runner | None = None
    pins: VersionPins | None = None
    cache: Stage12Cache | None = None
    build: Stage12Builder | None = None
    analyse: Callable[[FnaDocument], Any] | None = None
    gold_pos: Callable[[GoldCall], float | None] | None = None
    reference_transcript: Callable[[str], Transcript | None] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


_CONTEXT = EvalContext()


def configure(**kwargs: Any) -> EvalContext:
    """Set the runner, pins, cache, builder, FNA engine or reference-transcript lookup used by the task."""
    for k, v in kwargs.items():
        if not hasattr(_CONTEXT, k):
            raise TypeError(f"unknown eval setting {k!r}")
        setattr(_CONTEXT, k, v)
    return _CONTEXT


def reset() -> None:
    global _CONTEXT
    _CONTEXT = EvalContext()


def default_runner() -> Runner:
    """``advisor_intel.pipeline.process_cached_call``: stages 3–6 in-process on a cached call."""
    fn = lazy_attr("advisor_intel.pipeline", "process_cached_call")
    if fn is None:
        raise RuntimeError("advisor_intel.pipeline.process_cached_call is not installed; pass a runner with configure(runner=...)")
    return fn


_VERSION_FILE = re.compile(r"^v(\d+)\.md$")


def latest_prompt_versions(root: Path | None = None) -> dict[str, str]:
    """Each prompt component's newest version: {'extract_slot_group/income': 'v3', 'entailment': 'v2'}."""
    root = root or prompts_dir()
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for d in sorted({p.parent for p in root.rglob("v*.md")}):
        versions = [int(m.group(1)) for p in d.iterdir() if (m := _VERSION_FILE.match(p.name))]
        if versions:
            out[d.relative_to(root).as_posix()] = f"v{max(versions)}"
    return out


def _lexicon_version() -> str | None:
    lex_dir = PIPELINE_DIR / "advisor_intel" / "lexicon"
    files = sorted(lex_dir.glob("v*.yaml")) if lex_dir.exists() else []
    if not files:
        return None
    try:
        v = yaml.safe_load(files[-1].read_text()).get("version")
    except (OSError, AttributeError, yaml.YAMLError):
        return None
    return str(v) if v is not None else None


def current_version_pins(*, prompts_root: Path | None = None) -> VersionPins:
    """The pins this checkout would run with: model routing from settings, the newest prompt and lexicon versions,
    and ``ADVISOR_INTEL_PIN_<FIELD>`` overrides (e.g. ``ADVISOR_INTEL_PIN_CALIBRATOR_VERSION``)."""
    routing = get_settings().models
    pins = VersionPins(
        frontier_model=routing.frontier,
        reasoning_model=routing.reasoning,
        verifier_model=routing.verifier,
        small_model=routing.small,
        prompt_versions=latest_prompt_versions(prompts_root),
    )
    updates: dict[str, Any] = {}
    lex = _lexicon_version()
    if lex:
        updates["lexicon_version"] = lex
    for name in VersionPins.model_fields:
        v = os.environ.get(f"ADVISOR_INTEL_PIN_{name.upper()}")
        if v and name != "prompt_versions":
            updates[name] = v
    return pins.model_copy(update=updates)


# --------------------------------------------------------------------------- data and task


def eval_cases(
    dataset: str = SYNTHETIC_DATASET,
    *,
    store: DatasetStore | None = None,
    transcript_kinds: Sequence[str] | None = None,
    include_heldout: bool = True,
    only_heldout: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Eval cases ({input, expected, metadata, tags}) from a dataset."""
    rows = (store or get_store()).fetch(dataset)
    out = []
    for r in sorted(rows, key=lambda x: x.id):
        kind = (r.input or {}).get("transcript_kind", "asr")
        split = r.metadata.get("split", "dev")
        if transcript_kinds is not None and kind not in transcript_kinds:
            continue
        if (split == "heldout" and not include_heldout) or (only_heldout and split != "heldout"):
            continue
        out.append(
            {
                "input": dict(r.input or {}, dataset=dataset, row_id=r.id),
                "expected": r.expected,
                "metadata": dict(r.metadata, dataset=dataset, transcript_kind=kind),
                "tags": list(r.tags),
            }
        )
    return out[:limit] if limit else out


def load_cached_call(
    input: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    *,
    pins: VersionPins | None = None,
    cache: Stage12Cache | None = None,
    build: Stage12Builder | None = None,
) -> CachedCall:
    """The dataset call with its cached Stage 1–2 output (§15.1)."""
    pins = pins or _CONTEXT.pins or current_version_pins()
    meta = dict(metadata or {})
    meta.setdefault("dataset", input.get("dataset", SYNTHETIC_DATASET))
    record = DatasetRecord(id=str(input.get("row_id") or input["call_id"]), input=input, metadata=meta)
    return (cache or _CONTEXT.cache or Stage12Cache()).load_call(record, pins, build=build or _CONTEXT.build)


def run_stages_3_to_6_from_cache(input: dict[str, Any], hooks: Any = None) -> dict[str, Any]:
    """The experiment task: load the cached Stage 1–2 output, run stages 3–6 with the injected runner (the same
    harness code as production), and return the ``SystemCallOutput`` the scorers read."""
    pins = _CONTEXT.pins or current_version_pins()
    runner = _CONTEXT.runner or default_runner()
    call = load_cached_call(input, getattr(hooks, "metadata", None), pins=pins)
    result = run_sync(runner(call, pins))
    out = summarise_result(result, call=call, transcript_kind=call.transcript_kind)
    if call.transcript_kind == "asr" and _CONTEXT.reference_transcript is not None:
        ref = _CONTEXT.reference_transcript(call.call_id)
        if ref is not None:
            out = out.model_copy(update={"transcript_metrics": asr_metrics(ref, call.transcript)})
    return out.model_dump(mode="json")


# --------------------------------------------------------------------------- reports


class ItemReport(BaseModel):
    id: str
    call_id: str
    transcript_kind: str = "asr"
    scores: dict[str, float | None] = {}
    misses: dict[str, list[str]] = {}
    error: str | None = None


class EvalReport(BaseModel):
    """What one experiment measured: per-item score means, the summary scores and the items (for diffs)."""

    eval_name: str = EVAL_NAME
    dataset: str = SYNTHETIC_DATASET
    experiment: str | None = None
    created_at: str = ""
    git_sha: str | None = None
    pins: dict[str, Any] = {}
    n_items: int = 0
    n_errors: int = 0
    scores: dict[str, float | None] = {}
    summary: dict[str, Any] = {}
    items: list[ItemReport] = []

    @property
    def task_error_rate(self) -> float:
        return self.n_errors / self.n_items if self.n_items else 0.0

    def metric(self, name: str) -> float | None:
        """A score by name; dotted names reach nested summary values ('ece_by_group.income', 'by_group.income.slot_recall')."""
        if name == "task_error_rate":
            return self.task_error_rate
        if name in self.scores:
            return self.scores[name]
        node: Any = self.summary
        for part in name.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return float(node) if isinstance(node, (int, float)) and not isinstance(node, bool) else None


def _git_sha() -> str | None:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=PIPELINE_DIR, check=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def build_report(
    rows: Iterable[dict[str, Any]],
    *,
    eval_name: str = EVAL_NAME,
    dataset: str = SYNTHETIC_DATASET,
    experiment: str | None = None,
    pins: VersionPins | dict[str, Any] | None = None,
    analyse: Callable[[FnaDocument], Any] | None = None,
    gold_pos: Callable[[GoldCall], float | None] | None = None,
) -> EvalReport:
    """A report from scored rows ({id, input, output, expected, metadata, scores, misses, error})."""
    items, records = [], []
    for row in rows:
        inp = row.get("input") or {}
        meta = row.get("metadata") or {}
        kind = meta.get("transcript_kind") or inp.get("transcript_kind", "asr")
        items.append(
            ItemReport(
                id=str(row.get("id") or inp.get("row_id") or inp.get("call_id")),
                call_id=str(inp.get("call_id", "")),
                transcript_kind=kind,
                scores=dict(row.get("scores") or {}),
                misses=dict(row.get("misses") or {}),
                error=row.get("error"),
            )
        )
        if row.get("error") is None and row.get("output") is not None and row.get("expected") is not None:
            system = row["output"] if isinstance(row["output"], SystemCallOutput) else SystemCallOutput.model_validate(row["output"])
            gold = row["expected"] if isinstance(row["expected"], GoldCall) else GoldCall.model_validate(row["expected"])
            records.append(EvalRecord(system=system, gold=gold, metadata=dict(meta, transcript_kind=kind)))
    means: dict[str, float | None] = {}
    for name in ITEM_SCORE_NAMES:
        vals = [it.scores[name] for it in items if it.scores.get(name) is not None]
        means[name] = float(np.mean(vals)) if vals else None
    pins_dict = pins.model_dump(mode="json") if isinstance(pins, VersionPins) else dict(pins or {})
    return EvalReport(
        eval_name=eval_name,
        dataset=dataset,
        experiment=experiment,
        created_at=datetime.now(timezone.utc).isoformat(),
        git_sha=_git_sha(),
        pins=pins_dict,
        n_items=len(items),
        n_errors=sum(1 for it in items if it.error),
        scores=means,
        summary=summary_scores(records, analyse=analyse or _CONTEXT.analyse, gold_pos=gold_pos or _CONTEXT.gold_pos) if records else {},
        items=items,
    )


def report_path(eval_name: str = EVAL_NAME, dataset: str = SYNTHETIC_DATASET, *, latest: bool = True) -> Path:
    stamp = "latest" if latest else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return data_dir() / "evals" / "reports" / f"{eval_name}-{dataset}-{stamp}.json"


def save_report(report: EvalReport, path: Path | None = None) -> Path:
    """Write the report (and the ``-latest`` copy the CI gate step reads)."""
    latest = report_path(report.eval_name, report.dataset)
    write_json(latest, report.model_dump(mode="json"))
    if path is not None and path != latest:
        write_json(path, report.model_dump(mode="json"))
        return path
    return latest


def load_report(path: Path) -> EvalReport:
    return EvalReport.model_validate_json(Path(path).read_text())


def rows_path(eval_name: str = EVAL_NAME, dataset: str = SYNTHETIC_DATASET) -> Path:
    """The latest local run's scored rows (output and expected per item), read by the error analysis (§15.5)."""
    return data_dir() / "evals" / "rows" / f"{eval_name}-{dataset}-latest.jsonl"


def save_rows(rows: Sequence[dict[str, Any]], eval_name: str = EVAL_NAME, dataset: str = SYNTHETIC_DATASET) -> Path:
    path = rows_path(eval_name, dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    keep = ("id", "input", "output", "expected", "metadata", "error")
    tmp.write_text("".join(json.dumps(to_jsonable({k: r.get(k) for k in keep}), sort_keys=True) + "\n" for r in rows))
    tmp.replace(path)
    return path


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------- local runner


class _Hooks:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata


def run_local(
    cases: Sequence[dict[str, Any]],
    *,
    runner: Runner | None = None,
    pins: VersionPins | None = None,
    cache: Stage12Cache | None = None,
    build: Stage12Builder | None = None,
    analyse: Callable[[FnaDocument], Any] | None = None,
    gold_pos: Callable[[GoldCall], float | None] | None = None,
    eval_name: str = EVAL_NAME,
    dataset: str = SYNTHETIC_DATASET,
    experiment: str | None = None,
    save: bool | Path = True,
) -> EvalReport:
    """Run the experiment without Braintrust: same task, scorers and summary, written as a JSON report."""
    settings = {"runner": runner, "pins": pins, "cache": cache, "build": build, "analyse": analyse, "gold_pos": gold_pos}
    previous = {k: getattr(_CONTEXT, k) for k in settings}
    configure(**{k: v for k, v in settings.items() if v is not None})
    rows = []
    try:
        for case in cases:
            inp, meta = case["input"], case.get("metadata", {})
            row: dict[str, Any] = {
                "id": inp.get("row_id") or inp.get("call_id"),
                "input": inp,
                "metadata": meta,
                "expected": case.get("expected"),
            }
            try:
                out = run_stages_3_to_6_from_cache(inp, _Hooks(meta))
                metrics = item_metrics(SystemCallOutput.model_validate(out), GoldCall.model_validate(case["expected"]))
                row.update(
                    output=out,
                    scores={k: v.value for k, v in metrics.items()},
                    misses={k: v.misses[:20] for k, v in metrics.items() if v.misses},
                )
            except Exception as e:  # noqa: BLE001 - one failing call is an errored item, not a failed experiment
                row.update(output=None, scores={}, error=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}")
            rows.append(row)
        report = build_report(rows, eval_name=eval_name, dataset=dataset, experiment=experiment, pins=pins or _CONTEXT.pins)
    finally:
        configure(**previous)
    if save:
        save_report(report, save if isinstance(save, Path) else None)
        save_rows(rows, eval_name, dataset)
    return report


# --------------------------------------------------------------------------- Braintrust


def braintrust_scorers() -> list[ItemScorer]:
    """The per-item §15.3 scorers; the scorers of one item share a single ``item_metrics`` computation."""
    return list(ITEM_SCORERS)


def _report_from_braintrust(result: Any, *, eval_name: str, dataset: str, experiment: str | None) -> EvalReport:
    rows = []
    for r in result.results:
        inp = r.input if isinstance(r.input, dict) else {}
        rows.append(
            {
                "id": inp.get("row_id") or inp.get("call_id"),
                "input": inp,
                "output": r.output,
                "expected": r.expected,
                "metadata": r.metadata or {},
                "scores": dict(r.scores or {}),
                "error": None if r.error is None else f"{type(r.error).__name__}: {r.error}",
            }
        )
    return build_report(rows, eval_name=eval_name, dataset=dataset, experiment=experiment, pins=_CONTEXT.pins or current_version_pins())


def resolve_baseline(eval_name: str, dataset: str) -> EvalReport | None:
    """The baseline from ``ADVISOR_INTEL_EVAL_BASELINE``: a report path, or ``store:<ref>`` for the eval-baselines dataset."""
    spec = os.environ.get(BASELINE_ENV, "")
    if not spec:
        return None
    if spec.startswith("store:"):
        from .gate import BaselineStore

        return BaselineStore().get(eval_name, dataset, ref=spec.removeprefix("store:"))
    p = Path(spec)
    return load_report(p) if p.exists() else None


def gate_reporter(*, dataset: str, gate: bool | None = None, name: str = "advisor-intel-gate") -> Any:
    """A Braintrust reporter: builds the report and summary scores, writes it, and (when gating) compares it with the
    baseline; ``report_run`` returning False makes ``braintrust eval`` exit non-zero (§15.4)."""
    from braintrust import Reporter

    from .gate import compare

    def report_eval(evaluator: Any, result: Any, verbose: bool, jsonl: bool) -> EvalReport:
        experiment = getattr(result.summary, "experiment_name", None)
        rep = _report_from_braintrust(result, eval_name=EVAL_NAME, dataset=dataset, experiment=experiment)
        path = save_report(rep)
        print(f"[{EVAL_NAME}] {rep.n_items} items, {rep.n_errors} errors; report {path}")
        return rep

    def report_run(reports: list[EvalReport], verbose: bool, jsonl: bool) -> bool:
        do_gate = gate if gate is not None else os.environ.get(GATE_ENV, "0") == "1"
        ok = True
        for rep in reports:
            if not do_gate:
                continue
            baseline = resolve_baseline(rep.eval_name, rep.dataset)
            if baseline is None:
                print(f"[{EVAL_NAME}] no baseline for {rep.dataset}: gate skipped")
                continue
            res = compare(rep, baseline)
            print(res.markdown())
            ok = ok and res.passed
        return ok

    return Reporter(name, report_eval=report_eval, report_run=report_run)


def braintrust_eval(
    *,
    dataset: str = SYNTHETIC_DATASET,
    project: str | None = None,
    experiment_name: str | None = None,
    data: Any = None,
    no_send_logs: bool | None = None,
    gate: bool | None = None,
    max_concurrency: int | None = 4,
    **context: Any,
) -> Any:
    """Define (and, outside ``braintrust eval``, run) the extraction experiment:

    ``Eval("advisor-extraction", data=init_dataset("advisor-extraction", dataset), task=run_stages_3_to_6_from_cache,
    scores=[…per-item §15.3 scorers…], metadata=current_version_pins())``; summary scores come from the reporter.
    Without a Braintrust key the data is the local dataset and nothing is sent.
    """
    from braintrust import Eval, init_dataset

    configure(**context)
    keyed = braintrust_enabled()
    if no_send_logs is None:
        no_send_logs = not keyed
    if data is None:
        data = init_dataset(project or EVAL_PROJECT, dataset) if keyed else (lambda: eval_cases(dataset))
    pins = _CONTEXT.pins or current_version_pins()
    return Eval(
        project or EVAL_PROJECT,
        data=data,
        task=run_stages_3_to_6_from_cache,
        scores=braintrust_scorers(),
        experiment_name=experiment_name,
        metadata={"versions": pins.model_dump(mode="json"), "dataset": dataset, "eval": EVAL_NAME},
        reporter=gate_reporter(dataset=dataset, gate=gate),
        no_send_logs=no_send_logs,
        max_concurrency=max_concurrency,
    )


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the extraction experiment locally (no Braintrust) and write the report.")
    ap.add_argument("--dataset", default=SYNTHETIC_DATASET)
    ap.add_argument("--transcripts", default=None, help="comma-separated transcript kinds to include (human,asr,script)")
    ap.add_argument("--heldout", choices=["include", "exclude", "only"], default="include")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--experiment", default=None)
    args = ap.parse_args(argv)
    cases = eval_cases(
        args.dataset,
        transcript_kinds=args.transcripts.split(",") if args.transcripts else None,
        include_heldout=args.heldout != "exclude",
        only_heldout=args.heldout == "only",
        limit=args.limit,
    )
    if not cases:
        print(f"no cases in {args.dataset}", file=sys.stderr)
        return 2
    from .datasets import stage12_builder
    from .scorers import forecast_gold_pos

    build = _CONTEXT.build or stage12_builder()  # Stage 1–2 on a cache miss
    gold_pos = _CONTEXT.gold_pos or forecast_gold_pos()  # the gold profile's PoS through the forecast engine
    report = run_local(cases, dataset=args.dataset, experiment=args.experiment, save=args.out or True, build=build, gold_pos=gold_pos)
    print(f"{report.n_items} items, {report.n_errors} errors")
    for k, v in sorted(report.scores.items()):
        print(f"  {k}: {'-' if v is None else f'{v:.3f}'}")
    return 0 if report.n_errors < report.n_items else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EVAL_BASELINES",
    "EVAL_NAME",
    "EvalReport",
    "ItemReport",
    "braintrust_eval",
    "braintrust_scorers",
    "build_report",
    "configure",
    "current_version_pins",
    "eval_cases",
    "load_rows",
    "rows_path",
    "run_local",
    "run_stages_3_to_6_from_cache",
]
