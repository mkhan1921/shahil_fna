"""The CI gate (spec §15.4): an experiment against the main-branch baseline.

Rules: no tier A metric may regress by more than 2 points (0.02), and ECE may not worsen by more than 0.01 —
pooled, and for every slot group with enough labelled facts in both reports. The task error rate may not rise by
more than 5 points, because errored items would otherwise drop out of every average.

Baselines are ``EvalReport``s published from the main branch into the ``eval-baselines`` dataset (Braintrust when
keyed, local JSONL otherwise), or passed as a file.

    python -m advisor_intel.evals.gate check --current report.json --baseline-ref main [--markdown gate.md]
    python -m advisor_intel.evals.gate check --current report.json --baseline baseline.json
    python -m advisor_intel.evals.gate publish --report report.json --ref main

``check`` exits 1 when a rule fails (2 when ``--require-baseline`` is set and none exists) and appends the result
table to ``$GITHUB_STEP_SUMMARY`` when running in GitHub Actions.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel

from .datasets import EVAL_BASELINES, DatasetRecord, DatasetStore, get_store
from .extraction_eval import EvalReport, load_report
from .scorers import TIER_A_METRICS

TIER_A_MAX_REGRESSION = 0.02
ECE_MAX_WORSENING = 0.01
TASK_ERROR_MAX_RISE = 0.05
MIN_GROUP_FACTS = 30  # per-group ECE is gated only where both reports have this many labelled facts


class GateRule(BaseModel):
    metric: str  # a report metric name; dotted for nested summary values ("ece_by_group.income")
    direction: Literal["higher", "lower"]  # which way is better
    tolerance: float


class GateRow(BaseModel):
    metric: str
    baseline: float | None
    current: float | None
    delta: float | None
    tolerance: float
    direction: str
    passed: bool
    note: str = ""


class GateResult(BaseModel):
    passed: bool
    rows: list[GateRow]
    warnings: list[str] = []

    @property
    def failures(self) -> list[GateRow]:
        return [r for r in self.rows if not r.passed]

    def markdown(self) -> str:
        head = "✅ **Eval gate passed**" if self.passed else "❌ **Eval gate failed**"
        lines = [head, "", "| metric | baseline | current | Δ | allowed | |", "|---|---:|---:|---:|---:|---|"]

        def f(x: float | None) -> str:
            return "–" if x is None else f"{x:.4f}"

        for r in self.rows:
            allowed = f"≥ −{r.tolerance:g}" if r.direction == "higher" else f"≤ +{r.tolerance:g}"
            mark = "" if r.passed else "**regressed**"
            lines.append(f"| `{r.metric}` | {f(r.baseline)} | {f(r.current)} | {f(r.delta)} | {allowed} | {mark} {r.note}".rstrip() + " |")
        if self.warnings:
            lines += ["", *[f"- {w}" for w in self.warnings]]
        return "\n".join(lines)


def default_rules(current: EvalReport, baseline: EvalReport, *, min_group_facts: int = MIN_GROUP_FACTS) -> list[GateRule]:
    rules = [GateRule(metric=m, direction="higher", tolerance=TIER_A_MAX_REGRESSION) for m in TIER_A_METRICS]
    rules.append(GateRule(metric="ece", direction="lower", tolerance=ECE_MAX_WORSENING))
    cur_counts = current.summary.get("ece_counts") or {}
    base_counts = baseline.summary.get("ece_counts") or {}
    for g in sorted(set(cur_counts) & set(base_counts)):
        if cur_counts[g] >= min_group_facts and base_counts[g] >= min_group_facts:
            rules.append(GateRule(metric=f"ece_by_group.{g}", direction="lower", tolerance=ECE_MAX_WORSENING))
    rules.append(GateRule(metric="task_error_rate", direction="lower", tolerance=TASK_ERROR_MAX_RISE))
    return rules


def compare(current: EvalReport, baseline: EvalReport, *, rules: Sequence[GateRule] | None = None) -> GateResult:
    """Apply the §15.4 release gate. A metric the baseline has but the experiment lost fails; a metric neither has, or
    only the experiment has, passes with a note."""
    rules = list(rules) if rules is not None else default_rules(current, baseline)
    rows, warnings = [], []
    if current.dataset != baseline.dataset:
        warnings.append(f"comparing {current.dataset} with a {baseline.dataset} baseline")
    for rule in rules:
        b, c = baseline.metric(rule.metric), current.metric(rule.metric)
        if b is None:
            rows.append(
                GateRow(
                    metric=rule.metric,
                    baseline=None,
                    current=c,
                    delta=None,
                    tolerance=rule.tolerance,
                    direction=rule.direction,
                    passed=True,
                    note="no baseline value",
                )
            )
            continue
        if c is None:
            rows.append(
                GateRow(
                    metric=rule.metric,
                    baseline=b,
                    current=None,
                    delta=None,
                    tolerance=rule.tolerance,
                    direction=rule.direction,
                    passed=False,
                    note="not measured in this experiment",
                )
            )
            continue
        delta = c - b
        ok = delta >= -rule.tolerance - 1e-12 if rule.direction == "higher" else delta <= rule.tolerance + 1e-12
        rows.append(
            GateRow(metric=rule.metric, baseline=b, current=c, delta=delta, tolerance=rule.tolerance, direction=rule.direction, passed=ok)
        )
    return GateResult(passed=all(r.passed for r in rows), rows=rows, warnings=warnings)


class BaselineStore:
    """Main-branch reports in the ``eval-baselines`` dataset, one row per (eval, dataset, ref)."""

    def __init__(self, store: DatasetStore | None = None) -> None:
        self.store = store or get_store()

    @staticmethod
    def row_id(eval_name: str, dataset: str, ref: str) -> str:
        return f"{eval_name}:{dataset}:{ref}"

    def publish(self, report: EvalReport, ref: str = "main") -> str:
        slim = report.model_copy(update={"items": []})  # per-item rows stay in the experiment
        rec = DatasetRecord(
            id=self.row_id(report.eval_name, report.dataset, ref),
            input={"eval_name": report.eval_name, "dataset": report.dataset, "ref": ref},
            expected=slim.model_dump(mode="json"),
            metadata={"dataset": EVAL_BASELINES, "git_sha": report.git_sha, "created_at": report.created_at},
            tags=[f"ref:{ref}", f"dataset:{report.dataset}"],
        )
        return self.store.insert(EVAL_BASELINES, [rec])[0]

    def get(self, eval_name: str, dataset: str, ref: str = "main") -> EvalReport | None:
        wanted = self.row_id(eval_name, dataset, ref)
        for row in self.store.fetch(EVAL_BASELINES):
            if row.id == wanted and row.expected:
                return EvalReport.model_validate(row.expected)
        return None


def _write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as fh:
            fh.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The §15.4 eval gate.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check", help="compare an experiment report with the baseline; exit 1 on a regression")
    chk.add_argument("--current", type=Path, required=True)
    chk.add_argument("--baseline", type=Path, default=None, help="a baseline report file")
    chk.add_argument("--baseline-ref", default="main", help="the eval-baselines ref to compare with when no file is given")
    chk.add_argument("--require-baseline", action="store_true")
    chk.add_argument("--markdown", type=Path, default=None)
    pub = sub.add_parser("publish", help="store a report as the baseline for a ref")
    pub.add_argument("--report", type=Path, required=True)
    pub.add_argument("--ref", default="main")
    args = ap.parse_args(argv)

    if args.cmd == "publish":
        report = load_report(args.report)
        rid = BaselineStore().publish(report, args.ref)
        print(f"published baseline {rid}")
        return 0

    current = load_report(args.current)
    if args.baseline is not None:
        baseline = load_report(args.baseline) if args.baseline.exists() else None
    else:
        baseline = BaselineStore().get(current.eval_name, current.dataset, args.baseline_ref)
    if baseline is None:
        msg = f"no baseline for {current.eval_name}/{current.dataset}; gate {'failed' if args.require_baseline else 'skipped'}"
        print(msg)
        _write_summary(msg)
        return 2 if args.require_baseline else 0
    result = compare(current, baseline)
    text = result.markdown()
    print(text)
    _write_summary(text)
    if args.markdown:
        args.markdown.write_text(text + "\n")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
