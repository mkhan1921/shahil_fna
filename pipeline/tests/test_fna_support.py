"""The engine's supporting pieces (§12.7): finance (year steps against the TypeScript month loop, numpy
versions), JavaScript number semantics, ID numbers, batch bands, normaliseDocument and result dumps."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from datetime import UTC, date, datetime

import numpy as np
import pytest

from advisor_intel.fna_engine import analyse, analyse_batch, need_bands, need_shortfalls, parse_id
from advisor_intel.fna_engine import finance as fin
from advisor_intel.fna_engine._js import en_za, js_round, js_str, parse_date_ms, parse_local_date, to_fixed
from advisor_intel.fna_engine.defaults import new_document, normalise_document
from advisor_intel.fna_engine.id_number import exact_age, luhn_valid, parse_sa_id
from advisor_intel.fna_engine.results import Analysis, Finding, Line, NeedResult
from advisor_intel.fna_engine.tables import TS_TABLE_FIELDS
from advisor_intel.models.fna import FnaDocument

from .conftest import load_fixture

TODAY = date(2026, 10, 16)

# --------------------------------------------------------------------------- finance


def ts_fv_escalating(monthly: float, years: float, annual: float, escalation: float) -> float:
    """fvEscalatingContributions exactly as finance.ts writes it: month by month."""
    if monthly <= 0 or years <= 0:
        return 0.0
    r = (1 + annual) ** (1 / 12) - 1
    value, contribution = 0.0, monthly
    for m in range(1, int(js_round(years * 12)) + 1):
        value = value * (1 + r) + contribution
        if m % 12 == 0:
            contribution *= 1 + escalation
    return value


def test_year_steps_match_the_typescript_month_loop():
    rng = np.random.default_rng(3)
    cases = [(1.0, 15, 0.085, 0.055), (2_500, 0.4, 0.07, 0.045), (100, 40, 0.0, 0.0), (500, 25, 0.06, 0.06), (1, 7.25, -0.02, 0.1)]
    cases += [
        (float(rng.uniform(1, 20_000)), float(rng.uniform(0, 45)), float(rng.uniform(-0.05, 0.15)), float(rng.uniform(0, 0.1)))
        for _ in range(300)
    ]
    for monthly, years, annual, esc in cases:
        assert fin.fv_escalating_contributions(monthly, years, annual, esc) == pytest.approx(
            ts_fv_escalating(monthly, years, annual, esc), rel=1e-12
        )


def test_numpy_finance_equals_the_scalar_functions():
    rng = np.random.default_rng(5)
    n = 400
    m, y = rng.uniform(-100, 50_000, n), rng.choice([0, 0.5, 1, 7.3, 20, 41], n)
    d, g = rng.uniform(-0.02, 0.14, n), rng.uniform(0, 0.09, n)
    g[::7] = d[::7]  # equal discount and growth rates take the level branches
    checks = [
        (fin.pv_growing_monthly_income_vec(m, y, d, g), [fin.pv_growing_monthly_income(*a) for a in zip(m, y, d, g, strict=True)]),
        (fin.pv_growing_annuity_vec(m, y, d, g), [fin.pv_growing_annuity(*a) for a in zip(m, y, d, g, strict=True)]),
        (fin.fv_escalating_contributions_vec(m, y, d, g), [fin.fv_escalating_contributions(*a) for a in zip(m, y, d, g, strict=True)]),
        (fin.fv_vec(m, d, y), [fin.fv(*a) for a in zip(m, d, y, strict=True)]),
        (fin.pv_vec(m, d, y), [fin.pv(*a) for a in zip(m, d, y, strict=True)]),
        (fin.annuity_factor_vec(d / 12, y * 12), [fin.annuity_factor(a / 12, b * 12) for a, b in zip(d, y, strict=True)]),
        (fin.required_escalating_saving_vec(m, y, d, g), [fin.required_escalating_saving(*a) for a in zip(m, y, d, g, strict=True)]),
        (fin.loan_repayment_vec(m, d, y * 12), [fin.loan_repayment(a, b, c * 12) for a, b, c in zip(m, d, y, strict=True)]),
    ]
    for vec, scalar in checks:
        np.testing.assert_allclose(vec, scalar, rtol=1e-12, atol=1e-9)
    balance, pay = rng.uniform(0, 500_000, n), rng.uniform(0, 20_000, n)
    np.testing.assert_array_equal(
        fin.months_to_repay_vec(balance, d, pay), [fin.months_to_repay(a, b, c) for a, b, c in zip(balance, d, pay, strict=True)]
    )


def test_remaining_interest_on_a_level_loan():
    pmt = fin.loan_repayment(100_000, 0.12, 12)
    assert fin.remaining_interest(100_000, 0.12, pmt) == pytest.approx(pmt * 12 - 100_000, abs=0.05)
    assert fin.remaining_interest(100_000, 0.2, 1_000) == math.inf


# --------------------------------------------------------------------------- JavaScript number semantics


def test_number_text_follows_javascript():
    assert [to_fixed(x, 1) for x in (0.25, -0.25, 0.35, -0.04, -0.0, 17.5, 3.5000000000000004)] == [
        "0.3",
        "-0.3",
        "0.3",
        "-0.0",
        "0.0",
        "17.5",
        "3.5",
    ]
    assert [to_fixed(1.005, 2), to_fixed(2.5, 0), to_fixed(-2.5, 0), to_fixed(1e21, 2)] == ["1.00", "3", "-3", "1e+21"]
    assert [js_round(x) for x in (2.5, -2.5, 0.49999999999999994, -0.4)] == [3, -2, 0, 0] and math.copysign(1, js_round(-0.4)) < 0
    assert [js_str(x) for x in (3.0, 12.5, 0.1 + 0.2, 1e21, 1.5e-7, 123456789012345680000.0, -0.0, math.nan)] == [
        "3",
        "12.5",
        "0.30000000000000004",
        "1e+21",
        "1.5e-7",
        "123456789012345680000",
        "0",
        "NaN",
    ]
    assert [en_za(x) for x in (1234567, -1234567, -0.0, 999, 1234.5678, 1e21)] == [
        "1 234 567",
        "-1 234 567",
        "-0",
        "999",
        "1 234,568",
        "1 000 000 000 000 000 000 000",
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_number_text_matches_node_on_random_values():
    rng = np.random.default_rng(13)
    values = [float(x) for x in rng.uniform(-1, 1, 1500) * 10.0 ** rng.integers(-5, 13, 1500)]
    values += [float(x) for x in np.round(rng.uniform(-500, 5_000, 1500) * 8) / 8]  # exact halves and quarters
    script = (
        "const v=JSON.parse(require('fs').readFileSync(0,'utf8'));"
        "process.stdout.write(JSON.stringify(v.map(x=>[String(x),x.toFixed(1),x.toFixed(2),Math.round(x).toLocaleString('en-ZA')])))"
    )
    out = json.loads(subprocess.run(["node", "-e", script], input=json.dumps(values), capture_output=True, text=True, check=True).stdout)
    for x, (s, f1, f2, loc) in zip(values, out, strict=True):
        assert (js_str(x), to_fixed(x, 1), to_fixed(x, 2), en_za(js_round(x))) == (s, f1, f2, loc), x


def test_dates_parse_as_v8_does_in_a_utc_process():
    assert parse_local_date("2019-02-30") == date(2019, 3, 2)  # V8 rolls the day over
    assert parse_local_date("1986-04") == date(1986, 4, 1)
    assert [parse_local_date(s) for s in ("", "1986-4-12", "2019-13-01", "2019-01-32", "12/04/1986")] == [None] * 5
    assert parse_date_ms("2016-05-10") == datetime(2016, 5, 10, tzinfo=UTC).timestamp() * 1000  # date-only ISO strings are UTC
    assert parse_date_ms("2016-05-10T10:00:00.5+02:00") == datetime(2016, 5, 10, 8, 0, 0, 500_000, tzinfo=UTC).timestamp() * 1000
    assert math.isnan(parse_date_ms("10 May 2016"))


# --------------------------------------------------------------------------- ID numbers (§7.6)


def with_check_digit(first12: str) -> str:
    return next(first12 + d for d in "0123456789" if luhn_valid(first12 + d))


def test_parse_id_gives_date_of_birth_gender_and_citizenship():
    assert parse_id("8001015009087", TODAY) == (date(1980, 1, 1), "male", True)
    assert parse_id("8909030456085", TODAY) == (date(1989, 9, 3), "female", True)
    resident = with_check_digit("900215012318")
    assert parse_id(resident, TODAY) == (date(1990, 2, 15), "female", False)
    assert parse_sa_id(resident, TODAY).citizenship == "Permanent resident"
    assert parse_id("8001015009088", TODAY) is None
    assert parse_id(" 8001 0150 0908 7 ", TODAY) == (date(1980, 1, 1), "male", True)


def test_the_century_is_the_latest_one_that_is_not_in_the_future():
    born_2024 = with_check_digit("240229500908")
    born_1927 = with_check_digit("270101500908")
    assert parse_id(born_2024, TODAY)[0] == date(2024, 2, 29)
    assert parse_id(born_1927, TODAY)[0] == date(1927, 1, 1)
    assert parse_id(born_1927, date(2027, 6, 1))[0] == date(2027, 1, 1)
    assert parse_sa_id(with_check_digit("230229500908"), TODAY).error == "Date of birth digits are not a real date"
    assert parse_sa_id(with_check_digit("800101500938"), TODAY).error == "Citizenship digit must be 0, 1 or 2"


def test_exact_age_counts_milliseconds_like_a_javascript_date():
    assert exact_age("1986-04-12", datetime(2026, 4, 12)) == pytest.approx(40 * 365.25 / 365.2425, rel=1e-4)
    assert exact_age("", TODAY) is None


# --------------------------------------------------------------------------- batches and shortfall bands (§12.7)


def test_analyse_batch_equals_analysing_each_document():
    sample = FnaDocument.model_validate(load_fixture("sample_fna.json"))
    docs = [sample, new_document(), sample.model_copy(update={"assumptions": sample.assumptions.model_copy(update={"cpi": 0.06})})]
    batch = analyse_batch(docs, today=TODAY)
    assert [a.model_dump(mode="json") for a in batch] == [analyse(d, today=TODAY).model_dump(mode="json") for d in docs]


def test_need_bands_summarise_each_shortfall_over_the_draws():
    sample = FnaDocument.model_validate(load_fixture("sample_fna.json"))
    draws = []
    for i in range(41):
        doc = sample.model_copy(deep=True)
        doc.income["client"].grossMonthly = 60_000 + 2_000 * i
        draws.append(doc)
    analyses = analyse_batch(draws, today=TODAY)
    bands = need_bands(analyses)
    life = sorted(a.persons["client"].death.shortfall for a in analyses)
    assert bands["life.client"] == pytest.approx({"p10": life[4], "p50": life[20], "p90": life[36]})
    # Keyed as the app's review overlay keys its need lines (lib/review/impact.ts): "<area>.<person>" / "<area>.household".
    assert {
        "life.client",
        "income-protection.spouse",
        "estate.client",
        "retirement.spouse",
        "emergency.household",
        "education.household",
    } <= set(bands)
    assert {f"education.{d.id}" for d in sample.dependants if d.relationship == "child"} <= set(bands)
    assert all(b["p10"] <= b["p50"] <= b["p90"] for b in bands.values())
    single = analyse(new_document(), today=TODAY)
    assert "life.spouse" not in need_shortfalls(single)
    mixed = need_bands([single, analyses[0]])
    assert mixed["life.spouse"]["p50"] == analyses[0].persons["spouse"].death.shortfall  # summarised over the draws that have it


# --------------------------------------------------------------------------- normaliseDocument


def test_normalise_document_fills_partial_files_with_the_defaults():
    doc = normalise_document(
        {
            "client": {"firstName": "Ann"},
            "income": {"client": {"grossMonthly": 30_000}},
            "assets": [{"type": "shares", "value": 1_000}],
            "advice": {"recommendations": [{"area": "estate"}]},
            "futureField": 1,
        }
    )
    assert doc.client.firstName == "Ann" and doc.client.nationality == "South African"
    assert doc.income["client"].grossMonthly == 30_000 and doc.income["spouse"].uifApplies is True
    assert doc.assets[0].type == "shares" and doc.assets[0].bequeathToSpouse is True and doc.assets[0].id
    assert doc.advice.recommendations[0].area == "estate" and doc.advice.recommendations[0].premiumEscalation == 0.06
    assert doc.schemaVersion == 2 and doc.model_dump()["futureField"] == 1


# --------------------------------------------------------------------------- result dumps


def test_results_dump_like_the_typescript_json():
    a = analyse(FnaDocument.model_validate(load_fixture("sample_fna.json")), today=TODAY)
    data = a.model_dump(mode="json")
    assert list(data["table"]) == list(TS_TABLE_FIELDS)  # the reported TaxTable, not the whole table file
    assert a.table.livingAnnuity.maxDrawdown == 0.175  # ...while Python code still reaches the rest
    assert "person" not in data["emergency"] and data["persons"]["client"]["death"]["person"] == "client"
    assert "note" not in data["emergency"]["needLines"][0]
    assert all("person" in f for f in data["findings"] if f["area"] in ("life", "retirement"))
    assert Analysis.model_validate(data).model_dump(mode="json") == data  # the validating constructor reads it back
    assert Line(label="x", amount=1).model_dump() == {"label": "x", "amount": 1.0}
    assert Finding.build({"severity": "info", "area": "estate", "title": "t", "detail": "d"}).model_dump() == {
        "severity": "info",
        "area": "estate",
        "title": "t",
        "detail": "d",
    }
    need = a.emergency
    assert isinstance(need, NeedResult) and need.model_fields_set >= {"area", "need", "shortfall"}
