"""TypeScript ↔ Python parity of the FNA engine (§12.7, §15.3 "FNA engine port": 100% within R1 / 1e-6).

Without Node the committed subset (25 generated documents with their TypeScript results, and the catalogs)
and the exported sample analysis are checked; with Node the full harness runs: 2,000 fresh documents
through the TypeScript engine, then the same checks on every one. Regenerate the subset with
``python -m advisor_intel.fna_parity regenerate``.
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from advisor_intel.fna_engine import analyse
from advisor_intel.fna_parity.compare import compare
from advisor_intel.fna_parity.harness import FIXTURE, check, check_records, generate, node_unavailable_reason, read_records
from advisor_intel.models.fna import FnaDocument

from .conftest import load_fixture

SAMPLE_EXPORTED_AT = datetime.fromisoformat("2026-10-16T18:49:32.222+00:00")  # recovered from the fixture's exactAge


def test_python_reproduces_the_exported_sample_analysis():
    doc = FnaDocument.model_validate(load_fixture("sample_fna.json"))
    report = compare(load_fixture("sample_analysis.json"), analyse(doc, today=SAMPLE_EXPORTED_AT).model_dump(mode="json"))
    assert report.ok, "\n".join(report.diffs[:20])
    assert report.numbers > 1_000 and report.max_money < 1e-3 and report.max_ratio < 1e-9


def test_committed_subset_matches_typescript():
    summary = check_records(read_records(FIXTURE))
    assert summary.ok, summary.describe()
    assert summary.records == 25


def test_committed_subset_covers_the_household_types():
    tags = {t for rec in read_records(FIXTURE) if "tags" in rec for t in rec["tags"]}
    wanted = {
        "sample",
        "empty",
        "couple",
        "single",
        "retiree",
        "regime:icop",
        "regime:anc-accrual",
        "dependant:parent",
        "dependant:sibling",
        "tax:2025/26",
        "tax:2026/27",
        "fund:living-annuity",
        "policy:family-income",
    }
    assert wanted <= tags, wanted - tags


@pytest.mark.slow
def test_full_parity_on_2000_generated_documents(tmp_path, record_property):
    reason = node_unavailable_reason()
    if reason:
        pytest.skip(f"TypeScript side of the parity harness unavailable: {reason}")
    start = time.perf_counter()
    ts_seconds = generate(tmp_path, n=2000)
    summary = check(tmp_path)
    record_property("typescript_seconds", round(ts_seconds, 1))
    record_property("python_check_seconds", round(summary.seconds, 1))
    record_property("total_seconds", round(time.perf_counter() - start, 1))
    record_property("largest_money_gap", summary.max_money)
    record_property("largest_ratio_gap", summary.max_ratio)
    print(f"\n{summary.describe()}; TypeScript took {ts_seconds:.1f}s")
    assert summary.ok, summary.describe()
    assert summary.records == 2000
