"""Golden lexicon cases (spec §8.4): CI fails when a lexicon change moves any golden median by more than 10%."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import yaml

from advisor_intel.lexicon import CURRENT_VERSION, Lexicon, load_lexicon
from advisor_intel.lexicon.golden import MEDIAN_REL_TOL, GoldenCase, check_case, check_cases, load_cases, make_case

CASES_PATH = Path(__file__).parent / "lexicon_cases.yaml"
CASES = load_cases(CASES_PATH)
KINDS = {"approx", "vague_count", "vague_amount", "age_band", "relative", "rate", "likelihood", "certainty", "hedge", "scale"}


def test_golden_file_covers_every_section():
    assert len(CASES) >= 200
    assert len({c.id for c in CASES}) == len(CASES)
    assert {c.kind for c in CASES} == KINDS
    counts = Counter(c.kind for c in CASES)
    assert all(n >= 10 for n in counts.values()), counts
    assert yaml.safe_load(CASES_PATH.read_text())["lexicon_version"] == CURRENT_VERSION
    for c in CASES:
        assert c.p10 <= c.median <= c.p90 + 1e-9, c.id


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_golden_case_median_holds(case: GoldenCase):
    assert check_case(load_lexicon(), case) is None


def test_gate_fails_when_a_median_moves_more_than_ten_percent():
    text = load_lexicon().source_text
    moved = Lexicon.from_text(
        text.replace('"a few": {dist: triangular, low: 2, mode: 3, high: 5}', '"a few": {dist: triangular, low: 3, mode: 4, high: 6}')
    )
    failures = check_cases(moved, CASES)
    assert any("'a few'" in f for f in failures)
    assert any("'a few mil'" in f for f in failures)  # composites built on 'a few' move with it …
    assert not any("'a few hundred K'" in f for f in failures)  # … but 'a few hundred K' uses its own seed key
    nudged = Lexicon.from_text(
        text.replace('"a few": {dist: triangular, low: 2, mode: 3, high: 5}', '"a few": {dist: triangular, low: 2, mode: 3.1, high: 5}')
    )
    assert not check_cases(nudged, CASES)  # a change inside the 10% band passes


def test_gate_fails_when_a_key_disappears():
    text = load_lexicon().source_text.replace('  "omtrent": {dist: triangular, rel_halfwidth: 0.10}              # Afr. about\n', "")
    failures = check_cases(Lexicon.from_text(text), CASES)
    assert any("'omtrent'" in f and "now resolves as" in f for f in failures)


def test_make_case_round_trips():
    lex = load_lexicon()
    case = GoldenCase.model_validate(make_case(lex, "x-1", "age_band", "early sixties"))
    assert case.match == "exact" and case.median == pytest.approx(61.6292, rel=1e-4)
    assert check_case(lex, case) is None
    assert MEDIAN_REL_TOL == 0.10
