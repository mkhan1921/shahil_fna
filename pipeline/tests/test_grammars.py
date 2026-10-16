"""The §5.3 grammars: golden spoken forms (tests/grammar_cases.yaml) plus property tests."""

from __future__ import annotations

import math
import random
from pathlib import Path

import pytest
import yaml

from advisor_intel.ingest import grammars
from advisor_intel.ingest.grammars import ProductName
from advisor_intel.models.runs import VersionPins

CASES_FILE = Path(__file__).parent / "grammar_cases.yaml"
CASES = yaml.safe_load(CASES_FILE.read_text())["cases"]


def _same(a, b) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return str(a) == str(b)
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)


def _case_id(case: dict) -> str:
    return f"{case['kind']}:{case['text']}"


def test_case_file_has_at_least_300_cases():
    assert len(CASES) >= 300
    assert yaml.safe_load(CASES_FILE.read_text())["grammar_version"] == grammars.GRAMMAR_VERSION


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_grammar_case(case: dict):
    res = grammars.parse(case["text"], case["kind"])
    if case.get("accepted") is False:
        assert not res.accepted, f"expected rejection, got {[r.value for r in res.readings]}"
        return
    assert res.accepted, f"{case['text']!r} was not accepted"
    got = [r.value for r in res.readings]
    for v in case.get("values", []):
        assert any(_same(v, g) for g in got), f"missing reading {v!r}; got {got}"
    if case.get("exact"):
        assert len({str(round(g, 6)) if not isinstance(g, str) else g for g in got}) == len(case["values"]), f"extra readings: {got}"
    top = res.readings[0]
    if "top" in case:
        assert _same(top.value, case["top"]), f"top {top.value!r} != {case['top']!r}; all {got}"
    for attr, field in (
        ("currency", "currency"),
        ("period", "period"),
        ("scale", "scale_source"),
        ("key", "lexicon_key"),
        ("provider", "provider"),
    ):
        if attr in case:
            assert getattr(top, field) == case[attr], f"{field}: {getattr(top, field)!r} != {case[attr]!r}"
    if "rate" in case:
        assert top.is_rate is case["rate"]
    assert math.isclose(sum(r.weight for r in res.readings), 1.0, abs_tol=1e-6)


def test_version_matches_the_run_pin():
    assert grammars.GRAMMAR_VERSION == VersionPins().grammar_version


def test_grammars_compile_once():
    assert grammars.amount_grammar() is grammars.amount_grammar()
    assert grammars.age_grammar() is grammars.age_grammar()
    assert grammars.product_grammar() is grammars.product_grammar()


def test_two_fifty_has_three_scale_readings_and_none_wins_outright():
    res = grammars.parse("two fifty", "amount")
    by_value = {r.value: r for r in res.readings}
    assert set(by_value) == {250.0, 250_000.0, 2_500_000.0}
    assert by_value[2_500_000.0].scale_source == "implied_million"
    # Scale resolution (§7.4) decides; the grammar keeps them close.
    ws = sorted((r.weight for r in res.readings), reverse=True)
    assert ws[0] / ws[1] < 2


@pytest.mark.parametrize("seed", range(5))
def test_every_integer_round_trips_through_its_words(seed: int):
    rng = random.Random(seed)
    for _ in range(80):
        n = rng.choice([rng.randint(1, 999), rng.randint(1000, 999_999), rng.randint(1_000_000, 999_999_999)])
        for and_word in (True, False):
            text = grammars.int_to_words(n, and_word=and_word)
            res = grammars.parse(text, "amount")
            assert res.accepted, text
            values = [r.value for r in res.readings]
            assert float(n) in values, (text, values)
            if n >= 1000:
                assert res.readings[0].value == float(n), (text, values)


@pytest.mark.parametrize("value", [1_200_000, 250_000, 42_000, 1_500, 2_500_000, 850_000, 3_750_000, 18_800, 95_000, 1_000])
def test_spoken_forms_parse_back_to_the_amount(value: int):
    forms = grammars.amount_spoken_forms(value)
    assert forms
    for f in forms:
        res = grammars.parse(f, "amount")
        assert res.accepted, f
        assert any(r.value == float(value) for r in res.readings), (f, [r.value for r in res.readings])


def test_spec_spoken_forms_are_rendered():
    forms = grammars.amount_spoken_forms(1_200_000)
    for expected in ("one million two hundred thousand", "one point two million", "one comma two mil", "twelve hundred thousand"):
        assert expected in forms


def test_lattice_keeps_alternative_paths_weighted_by_asr_score():
    res = grammars.lattice_readings([[("two", 0.95)], [("fifty", 0.6), ("fifteen", 0.3)]], "amount")
    values = {r.value: r.weight for r in res.readings}
    for v in (250, 250_000, 2_500_000, 215, 215_000, 2_150_000):
        assert float(v) in values
    assert values[250.0] > values[215.0]
    assert values[250_000.0] / values[215_000.0] == pytest.approx(2.0, rel=1e-3)


def test_lattice_recovers_a_number_heard_only_in_the_alternatives():
    # 1-best "for thousand" is not an amount; the alternative "four" makes it one.
    res = grammars.lattice_readings([[("for", 0.7), ("four", 0.25)], [("thousand", 0.9)]], "amount")
    assert res.accepted
    assert res.readings[0].value == 4000.0
    assert res.best_input == "four thousand"


def test_lattice_rejects_when_no_path_is_well_formed():
    res = grammars.lattice_readings([[("too", 0.8), ("to", 0.2)], [("much", 0.9)]], "amount")
    assert not res.accepted and res.readings == []


def test_merge_keeps_the_provider_itn_as_one_more_candidate():
    spoken = grammars.parse("two fifty", "amount").readings
    itn = grammars.parse("R250", "amount").readings
    merged = grammars.merge_readings(spoken, itn, 0.2)
    assert math.isclose(sum(r.weight for r in merged), 1.0)
    values = {r.value for r in merged}
    assert {250.0, 250_000.0, 2_500_000.0} <= values
    alone = grammars.merge_readings([], itn, 0.2)
    assert alone[0].value == 250.0 and alone[0].currency == "ZAR"


def test_client_specific_product_names_compile_per_call():
    extra = (
        ProductName("Highveld Logistics Pension Fund", "pension", "Employer umbrella fund"),
        ProductName("Brightwater", None, "Brightwater"),
    )
    res = grammars.parse("highveld logistics pension fund", "product", extra_products=extra)
    assert res.accepted
    assert res.readings[0].value == "pension" and res.readings[0].provider == "Employer umbrella fund"
    assert res.readings[0].lexicon_key == "client"
    assert not grammars.parse("highveld logistics pension fund", "product").accepted
    combo = grammars.parse("brightwater provident fund", "product", extra_products=extra)
    assert combo.readings[0].value == "provident" and combo.readings[0].provider == "Brightwater"
    assert grammars.product_grammar(extra) is grammars.product_grammar(extra)


def test_format_and_written_forms():
    r = grammars.parse("two hundred and fifty thousand rand a month", "amount").readings[0]
    assert grammars.format_reading(r, "amount") == "R250 000 a month"
    assert grammars.written_amount(1_250_000.5, "ZAR") == "R1 250 000.5"
    rate = grammars.parse("eleven and a half percent", "amount").readings[0]
    assert grammars.format_reading(rate, "amount") == "11.5%"
    age = grammars.parse("late fifties", "age").readings[0]
    assert grammars.format_reading(age, "age") == "age 58.5 (late fifties)"
    prod = grammars.parse("allan gray ra", "product").readings[0]
    assert grammars.format_reading(prod, "product") == "retirement-annuity (Allan Gray)"


def test_number_to_words():
    assert grammars.int_to_words(250_000) == "two hundred and fifty thousand"
    assert grammars.int_to_words(1_200_005) == "one million two hundred thousand and five"
    assert grammars.int_to_words(1_200_005, and_word=False) == "one million two hundred thousand five"
    assert grammars.number_to_words(2.5) == "two point five"
    assert grammars.number_to_words(1.25, point="comma") == "one comma two five"


def test_token_normalisation():
    assert grammars.normalize_text("R.A. twenty-five p.m.") == "ra twenty five pm"
    assert grammars.normalize_text("twee-honderd 'n maand,") == "twee honderd 'n maand"
    assert grammars.normalize_text("R1,2m.") == "r1,2m"
    assert grammars.normalize_text("“Tax-free”") == "tax free"
