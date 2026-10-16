"""Performance targets (spec §12.6, §10.2): a full run of 10,000 paths x 60 years under 5 s on one core, a
1,000-path sensitivity run under 0.5 s, and about 30 sensitivity candidates well under a minute.

Timings are printed (run with -s to see them). Each target is measured after one warm-up run in the same process
(imports, table loading and the allocator's first growth are paid once per worker, not per forecast), as the
process's CPU time: "on one core" is what the targets mean, and CPU time does not grow when other processes
share the machine (a parallel test run), while wall-clock time does.
"""

from __future__ import annotations

import time

import pytest

from advisor_intel.forecast import sensitivity_runs
from advisor_intel.forecast.config import load_cma, load_config
from advisor_intel.forecast.mapper import build_plan
from advisor_intel.forecast.run import Forecaster
from tests.forecast_support import StubTaxModel
from tests.test_forecast_consistency import _profile_with_uncertain_facts

pytestmark = pytest.mark.slow


def _tax():
    try:
        from advisor_intel.forecast.tax import default_tax_model

        return default_tax_model()
    except Exception:  # noqa: BLE001 - without the FNA engine the stub has the same shape
        return StubTaxModel()


def _forecaster():
    prof, doc = _profile_with_uncertain_facts()
    return prof, doc, Forecaster(build_plan(prof, base_document=doc, config=load_config(), cma=load_cma()), _tax())


def _best_of(fn, repeats: int = 3) -> tuple[float, float]:
    """The fastest of ``repeats`` calls: (CPU seconds of this process, wall-clock seconds)."""
    times = []
    for _ in range(repeats):
        c0, t0 = time.process_time(), time.perf_counter()
        fn()
        times.append((time.process_time() - c0, time.perf_counter() - t0))
    return min(times)


def test_full_run_of_10000_paths_and_60_years_under_5_seconds():
    prof, doc, fc = _forecaster()
    t0 = time.perf_counter()
    first = fc.run(1, 10_000)
    cold = time.perf_counter() - t0
    assert first.inp.n_years == 60 and first.result.n_paths == 10_000
    cpu, wall = _best_of(lambda: Forecaster(build_plan(prof, base_document=doc, config=fc.config, cma=fc.cma), fc.tax).run(2, 10_000))
    print(f"\nfull run, 10,000 paths x 60 years: first {cold:.2f}s, warm {cpu:.2f}s CPU ({wall:.2f}s wall)")
    assert cpu < 5.0


def test_sensitivity_run_of_1000_paths_under_half_a_second():
    _, _, fc = _forecaster()
    fc.run(1, 1_000)
    cpu, wall = _best_of(lambda: fc.run(2, 1_000), repeats=5)
    print(f"\nreduced run, 1,000 paths: {cpu:.3f}s CPU ({wall:.3f}s wall)")
    assert cpu < 0.5


def test_thirty_sensitivity_candidates_well_under_a_minute():
    prof, doc, fc = _forecaster()
    facts = [f for f in sorted(prof.facts) if f in fc.plan.samplers and fc.plan.samplers[f].tier in ("A", "B")][:30]
    assert len(facts) == 30
    c0, t0 = time.process_time(), time.perf_counter()
    rows = sensitivity_runs(prof, facts, n_paths=1_000, seed=5, base_document=doc, config=fc.config, cma=fc.cma, tax_model=fc.tax)
    cpu, wall = time.process_time() - c0, time.perf_counter() - t0
    print(f"\nsensitivity, 30 candidates x 1,000 paths: {cpu:.1f}s CPU ({wall:.1f}s wall)")
    assert len(rows) == 30 and cpu < 30
