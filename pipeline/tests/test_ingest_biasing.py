"""Per-call amount biasing and the custom vocabulary (§5.3, §5.4)."""

from __future__ import annotations

from datetime import date

from advisor_intel.ingest import grammars
from advisor_intel.ingest.asr import DeepgramASR
from advisor_intel.ingest.biasing import (
    amount_phrases,
    build_vocabulary,
    call_phrases,
    load_vocab_file,
    prior_ages,
    prior_amounts,
    prior_names,
    product_names_for_call,
)
from advisor_intel.ingest.types import CrmNames
from advisor_intel.models.fna import FnaDocument

ANCHOR = date(2026, 10, 16)


def test_static_vocabulary_file_seeds_section_5_4():
    v = load_vocab_file()
    terms = {p.text for p in v.phrases}
    for t in (
        "retirement annuity",
        "RA",
        "preservation fund",
        "provident fund",
        "pension fund",
        "living annuity",
        "life annuity",
        "TFSA",
        "tax-free savings",
        "two-pot",
        "savings pot",
        "retirement pot",
        "vested pot",
        "Regulation 28",
        "endowment",
        "unit trust",
        "ETF",
        "offshore allowance",
        "discretionary investment",
        "SARS",
        "JSE",
        "Allan Gray",
        "Old Mutual",
        "Discovery",
    ):
        assert t in terms, t
    tfsa = next(p for p in v.phrases if p.text == "TFSA")
    assert tfsa.sounds_like == ["tee eff ess ay"] and tfsa.kind == "vocab" and tfsa.source.endswith(":products")


def test_amount_spoken_forms_and_distractors():
    phrases = amount_phrases(1_200_000, source="assets[x].value")
    known = [p.text for p in phrases if p.kind == "amount"]
    assert {"one point two million", "one comma two mil", "twelve hundred thousand", "one million two hundred thousand"} <= set(known)
    assert all(grammars.parse(t, "amount").readings[0].value == 1_200_000.0 for t in known)
    distractors = [p for p in phrases if p.kind == "distractor"]
    assert {p.source for p in distractors} == {"assets[x].value×10", "assets[x].value×0.1"}
    values = {grammars.parse(p.text, "amount").readings[0].value for p in distractors}
    assert {12_000_000.0, 120_000.0} <= values
    assert all(p.boost < phrases[0].boost for p in distractors)


def test_prior_fna_amounts_ages_and_names(sample_fna_json):
    doc = FnaDocument.model_validate(sample_fna_json)
    amounts = dict(prior_amounts(doc))
    fund = doc.retirementFunds[0]
    assert amounts[f"retirementFunds[{fund.id}].value"] == 1_320_000
    assert amounts["income.client.grossMonthly"] == 100_000
    assert all(v != 0 for v in amounts.values())
    ages = dict(prior_ages(doc, ANCHOR))
    assert ages["client.dateOfBirth"] == 40 and ages["retirement.client.retirementAge"] == 65
    names = {n for _, n, _ in prior_names(doc)}
    assert {"Thabo", "Thabo Mokoena", "Lerato Mokoena", "Neo Mokoena", "Neo", "Highveld Logistics Pension Fund"} <= names
    assert "Bank" not in names  # generic placeholders are not boosted


def test_call_vocabulary_ranks_and_respects_provider_limits(sample_fna_json):
    doc = FnaDocument.model_validate(sample_fna_json)
    crm = CrmNames(advisor=["Sipho Dlamini"], client_primary=["Thabo Mokoena"], client_secondary=["Lerato Mokoena"])
    vocab = build_vocabulary(doc, anchor=ANCHOR, crm=crm)
    texts = [p.text for p in vocab.phrases]
    assert "one million three hundred and twenty thousand" in texts and "one point three two million" in texts
    assert "forty" in texts and "Sipho Dlamini" in texts and "TFSA" in texts
    kinds = {p.kind for p in vocab.phrases}
    assert kinds == {"name", "product", "amount", "age", "vocab", "distractor"}
    top = vocab.select(100)
    assert len(top) == 100
    counts = {k: sum(1 for p in top if p.kind == k) for k in kinds}
    assert counts["name"] >= 1 and counts["vocab"] >= 20 and counts["amount"] >= 20 and counts["distractor"] >= 5
    order = [p.kind for p in top]
    assert order.index("name") < order.index("amount") < order.index("vocab") < order.index("distractor")
    assert all(len(t.split()) <= 6 for t in vocab.terms(1000, max_words=6))
    # Deepgram gets at most its 100 keyterms, highest priority first.
    params = DeepgramASR(api_key="k").params(None, vocab, True)
    assert sum(1 for k, _ in params if k == "keyterm") == 100
    assert len(call_phrases(None, anchor=ANCHOR)) == 0


def test_product_names_for_the_per_call_grammar(sample_fna_json):
    doc = FnaDocument.model_validate(sample_fna_json)
    names = product_names_for_call(doc)
    by_surface = {n.surface: n for n in names}
    hv = by_surface["Highveld Logistics Pension Fund"]
    assert hv.product_type == "pension" and hv.provider == "Employer umbrella fund"
    assert "Bank" not in by_surface and "Retirement annuity" not in by_surface  # generic or already in the catalogue
    res = grammars.parse("highveld logistics pension fund", "product", extra_products=names)
    assert res.accepted and res.readings[0].value == "pension"
    assert product_names_for_call(None) == ()
