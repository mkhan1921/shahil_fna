"""Timing targets for the FNA engine (§12.7): under 1 ms per document and under 2 s for ``analyse_batch``
over 1,000 materialised draws. Timings vary between machines, so the limits are the targets times
``FNA_PERF_SLACK`` (default 1.5, for shared runners); the measured numbers are printed and recorded."""

from __future__ import annotations

import os
import time
from datetime import date

import numpy as np
import pytest

from advisor_intel.fna_engine import analyse, analyse_batch, need_bands
from advisor_intel.fna_parity.harness import FIXTURE, parse_now, read_records
from advisor_intel.models.fna import FnaDocument

from .conftest import load_fixture

SLACK = float(os.environ.get("FNA_PERF_SLACK", "1.5"))
TODAY = date(2026, 10, 16)


def _draws(n: int) -> list[FnaDocument]:
    """Materialised-draw stand-ins: the sample household with incomes, values and retirement ages varied."""
    sample = FnaDocument.model_validate(load_fixture("sample_fna.json"))
    rng = np.random.default_rng(1)
    out = []
    for _ in range(n):
        d = sample.model_copy(deep=True)
        for k in ("client", "spouse"):
            d.income[k].grossMonthly *= float(rng.lognormal(0, 0.1))
        d.retirement["client"].retirementAge = float(rng.choice([60, 63, 65, 67]))
        for a in d.assets:
            a.value *= float(rng.lognormal(0, 0.15))
        out.append(d)
    return out


@pytest.mark.slow
def test_analyse_takes_under_a_millisecond_per_document(record_property):
    docs = [(FnaDocument.model_validate(r["doc"]), parse_now(r["now"])) for r in read_records(FIXTURE) if "doc" in r]
    sample = FnaDocument.model_validate(load_fixture("sample_fna.json"))
    for d, now in docs:
        analyse(d, today=now)  # warm the caches
    mixed = []
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(8):
            for d, now in docs:
                analyse(d, today=now)
        mixed.append((time.perf_counter() - start) / (8 * len(docs)))
    rich = []
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(100):
            analyse(sample, today=TODAY)
        rich.append((time.perf_counter() - start) / 100)
    record_property("ms_per_document_mixed", round(min(mixed) * 1e3, 3))
    record_property("ms_per_document_sample_household", round(min(rich) * 1e3, 3))
    print(
        f"\nanalyse: {min(mixed) * 1e3:.3f} ms per document over the 25 parity documents, {min(rich) * 1e3:.3f} ms for the sample household"
    )
    assert min(mixed) < 0.001 * SLACK


@pytest.mark.slow
def test_a_batch_of_1000_draws_takes_under_two_seconds(record_property):
    draws = _draws(1000)
    analyse_batch(draws[:20], today=TODAY)
    start = time.perf_counter()
    analyses = analyse_batch(draws, today=TODAY)
    seconds = time.perf_counter() - start
    start = time.perf_counter()
    bands = need_bands(analyses)
    band_seconds = time.perf_counter() - start
    record_property("batch_seconds", round(seconds, 3))
    print(f"\nanalyse_batch: {seconds:.2f}s for 1,000 draws; need_bands {band_seconds:.2f}s")
    assert len(analyses) == 1000 and bands["life.client"]["p10"] <= bands["life.client"]["p90"]
    assert seconds < 2.0 * SLACK


@pytest.mark.slow
def test_materialising_a_draw_takes_about_a_millisecond(record_property):
    """The K documents the need bands analyse must come as fast as the engine reads them (§12.7: about 1 ms a
    document): at most 1.5 ms per document for K ≥ 100, measured as CPU time so other load on the machine does
    not count. Two shapes: the sample household's prior with uncertain call facts on top (380 facts over a base
    document), and the same prior rebuilt into an empty document (every row created from the facts)."""
    from advisor_intel import binding
    from tests.forecast_support import prior_profile, sample_document
    from tests.test_forecast_consistency import _profile_with_uncertain_facts

    prof, doc = _profile_with_uncertain_facts()
    cases = {"prior_with_call_facts": (prof, doc), "prior_into_an_empty_document": (prior_profile(sample_document()), None)}
    for name, (profile, base) in cases.items():
        binding.materialise_draws(profile, 20, seed=0, base=base)  # warm the catalog and the first allocations
        best = float("inf")
        for seed in range(3):
            start = time.process_time()
            docs = binding.materialise_draws(profile, 200, seed=seed, base=base)
            best = min(best, (time.process_time() - start) / len(docs))
        record_property(f"ms_per_draw_document_{name}", round(best * 1e3, 3))
        print(f"\nmaterialise_draws, {name}: {best * 1e3:.3f} ms per document")
        assert best < 0.0015 * SLACK, name
