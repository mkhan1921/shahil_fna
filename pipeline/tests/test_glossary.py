"""Unknown terms and the practice glossary (spec §8.5)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from advisor_intel.config import get_settings
from advisor_intel.lexicon import (
    GlossaryEntry,
    GlossaryStore,
    InMemoryGlossary,
    JsonGlossary,
    TermCandidate,
    TermContext,
    WebResult,
    confirm_lookup,
    confirm_term,
    is_unknown,
    load_catalogue,
    lookup_term,
)
from advisor_intel.models.fna import FnaDocument

CTX = TermContext()
AT = datetime(2026, 10, 16, 9, 30, tzinfo=UTC)


def lookup(term: str, context: TermContext = CTX, glossary: GlossaryStore | None = None, **kw):
    return lookup_term(term, context, glossary=glossary or InMemoryGlossary(), **kw)


class FakeSearch:
    """An injected web search; records its calls."""

    def __init__(self, results):
        self.results = results
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, query, domains):
        self.calls.append((query, list(domains)))
        if isinstance(self.results, Exception):
            raise self.results
        return self.results


# --------------------------------------------------------------------------- catalogue


def test_seed_catalogue_maps_to_fna_types():
    cat = load_catalogue()
    by_name = {e.name: e for e in cat.entries()}
    assert (by_name["retirement annuity"].kind, by_name["retirement annuity"].fna_type, by_name["retirement annuity"].fna_list) == (
        "fund",
        "retirement-annuity",
        "retirementFunds",
    )
    assert by_name["tax-free savings account"].fna_type == "tfsa" and by_name["tax-free savings account"].fna_list == "assets"
    assert by_name["living annuity"].fna_type == "living-annuity" and by_name["two-pot savings component"].fna_type == "savingsPot"
    assert by_name["income protection"].fna_list == "policies" and by_name["home loan"].fna_list == "liabilities"
    assert by_name["group life cover"].is_group
    for name in (
        "Discovery",
        "Old Mutual",
        "Sanlam",
        "Liberty",
        "Momentum",
        "Allan Gray",
        "Coronation",
        "Ninety One",
        "Sygnia",
        "Satrix",
        "10X",
    ):
        assert by_name[name].kind == "provider" and by_name[name].domain
    lines = cat.context_lines(practice_providers=["Acme Wealth"])
    assert any(line.startswith("retirement annuity (RA") for line in lines) and "Acme Wealth" in lines[-2]


@pytest.mark.parametrize(
    ("term", "product_type", "provider"),
    [
        ("RA", "retirement-annuity", None),
        ("my RA", "retirement-annuity", None),
        ("tax free savings", "tfsa", None),
        ("TFSA", "tfsa", None),
        ("living annuity", "living-annuity", None),
        ("provident fund", "provident", None),
        ("GEPF", "pension", None),
        ("two-pot", "savingsPot", None),
        ("endowment", "endowment", None),
        ("unit trusts", "unit-trust", None),
        ("ETF", "shares", None),
        ("the bond", "home-loan", None),
        ("burial society", "funeral", None),
        ("dread disease", "severe-illness", None),
        ("stokvel", "cash", None),
        ("my Allan Gray RA", "retirement-annuity", "Allan Gray"),
        ("Sanlam funeral policy", "funeral", "Sanlam"),
        ("Greenlight", "life", "Old Mutual"),
        ("Myriad", "life", "Momentum"),
        ("Satrix 40", "shares", "Satrix"),
        ("Alan Gray", None, "Allan Gray"),
    ],
)
def test_catalogue_resolves_sa_products_and_providers(term, product_type, provider):
    r = lookup(term)
    assert r.resolved and r.source == "catalogue" and r.searched == ["glossary", "catalogue"], r
    assert r.product_type == product_type and r.candidate.provider == provider
    assert r.candidate.score >= 0.8


def test_preservation_fund_needs_context():
    r = lookup("preservation fund", TermContext(text="I moved my old company money into a preservation fund"))
    assert not r.resolved and r.product_type == "other"
    assert {c.product_type for c in r.candidates[:2]} == {"preservation-pension", "preservation-provident"}
    assert "without a clear winner" in r.reason
    pension = lookup("preservation fund", TermContext(text="the pension from my last job went into a preservation fund"))
    assert pension.resolved and pension.product_type == "preservation-pension"
    provident = lookup("preservation fund", TermContext(text="my provident fund money is in a preservation fund"))
    assert provident.product_type == "preservation-provident"


def test_generic_words_and_lookalikes_do_not_resolve():
    for term in ("Plus", "Balanced", "Max", "Ace Investments", "Zorblax Wealth Booster"):
        r = lookup(term)
        assert not r.resolved and r.product_type == "other", term


def test_practice_lists_are_searched_with_the_catalogue():
    ctx = TermContext(practice_providers=["Acme Wealth Partners"], practice_products=["Acme Growth Wrap"])
    r = lookup("Acme Growth Wrap", ctx)
    assert r.resolved and r.candidate.source == "practice"
    assert lookup("Acme Wealth Partners", ctx).candidate.provider == "Acme Wealth Partners"


# --------------------------------------------------------------------------- glossary first, then web


def test_glossary_is_searched_first():
    g = InMemoryGlossary(
        [
            GlossaryEntry(
                term="the green policy",
                aliases=["green one"],
                canonical_name="Discovery Life Plan",
                product_type="life",
                provider="Discovery",
                kind="policy",
                confirmed_by="adviser-7",
                confirmed_at=AT,
            )
        ]
    )
    web = FakeSearch([])
    r = lookup("the green policy", glossary=g, web_search=web)
    assert r.resolved and r.source == "glossary" and r.searched == ["glossary"] and r.product_type == "life"
    assert lookup("green one", glossary=g).candidate.canonical_name == "Discovery Life Plan"
    assert web.calls == []


def test_web_search_is_the_last_resort_and_allow_listed():
    web = FakeSearch(
        [
            WebResult(url="https://www.example.com/zorblax", title="Zorblax Wealth Booster", snippet="A life cover policy"),
            {
                "url": "https://www.sanlam.co.za/zorblax",
                "title": "Zorblax Wealth Booster - Sanlam",
                "snippet": "An endowment policy for five years",
            },
        ]
    )
    r = lookup("Zorblax Wealth Booster", web_search=web)
    assert r.resolved and r.source == "web" and r.searched == ["glossary", "catalogue", "web"]
    assert r.candidate.source_url == "https://www.sanlam.co.za/zorblax" and r.candidate.provider == "Sanlam"
    assert r.product_type == "endowment" and r.candidate.score == pytest.approx(0.95)
    assert all("example.com" not in (c.source_url or "") for c in r.candidates)  # outside the allow-list: ignored
    assert web.calls == [("Zorblax Wealth Booster", get_settings().web_search_allowlist)]
    assert lookup("RA", web_search=web) and len(web.calls) == 1  # never called when the catalogue resolves


def test_web_search_failures_leave_the_term_unresolved():
    r = lookup("Zorblax Wealth Booster", web_search=FakeSearch(RuntimeError("timeout")))
    assert not r.resolved and r.product_type == "other" and r.searched[-1] == "web"
    only_bad = FakeSearch([WebResult(url="https://evil.example.org/x", title="Zorblax Wealth Booster")])
    assert not lookup("Zorblax Wealth Booster", web_search=only_bad).resolved
    narrow = FakeSearch([WebResult(url="https://www.sanlam.co.za/z", title="Zorblax Wealth Booster")])
    assert not lookup("Zorblax Wealth Booster", web_search=narrow, allowlist=["discovery.co.za"]).resolved


def test_unresolved_terms_record_candidates_and_block_tier_a():
    r = lookup("preservation fund", TermContext(text="a preservation fund"))
    rec = r.as_term_resolution()
    assert rec["status"] == "unresolved" and rec["product_type"] == "other" and len(rec["candidates"]) >= 2
    flag = r.blocking_flag("retirementFunds[].type#account.pres_1", tier="A")
    assert flag is not None and flag.kind == "unresolved_term" and flag.fact_id == "retirementFunds[].type#account.pres_1"
    assert r.blocking_flag("x", tier="B") is None
    ok = lookup("RA")
    assert ok.as_term_resolution() == {"term": "RA", "candidate": ok.candidate.model_dump(mode="json"), "source": "catalogue"}
    assert ok.blocking_flag("x", tier="A") is None


# --------------------------------------------------------------------------- memory


def test_confirmed_answers_are_remembered():
    g = InMemoryGlossary()
    web = FakeSearch([WebResult(url="https://www.sanlam.co.za/z", title="Zorblax Wealth Booster", snippet="endowment")])
    first = lookup("Zorblax Wealth Booster", glossary=g, web_search=web)
    assert first.source == "web" and g.entries() == []  # web answers enter the glossary only once confirmed
    entry = confirm_lookup(g, first, confirmed_by="adviser-7", at=AT)
    assert (entry.source, entry.confirmed_by, entry.confirmed_at, entry.source_url) == (
        "web",
        "adviser-7",
        AT,
        "https://www.sanlam.co.za/z",
    )
    again = lookup("Zorblax Wealth Booster", glossary=g, web_search=web)
    assert again.source == "glossary" and len(web.calls) == 1


def test_reviewer_corrections_and_required_confirmer():
    g = InMemoryGlossary()
    r = lookup("preservation fund", TermContext(text="a preservation fund"), glossary=g)
    with pytest.raises(ValueError):
        confirm_lookup(g, r, confirmed_by="adviser-7")
    fix = TermCandidate(
        canonical_name="Old Mutual provident preservation fund",
        product_type="preservation-provident",
        provider="Old Mutual",
        kind="fund",
        source="reviewer",
        score=1.0,
    )
    entry = confirm_lookup(g, r, confirmed_by="adviser-7", correction=fix)
    assert entry.source == "reviewer" and g.get("Preservation Fund") == entry
    with pytest.raises(ValueError):
        confirm_term(g, "x", canonical_name="y", confirmed_by="")


def test_json_glossary_persists(tmp_path):
    path = tmp_path / "glossary" / "practice.json"
    g = JsonGlossary(path)
    confirm_term(
        g,
        "the green policy",
        canonical_name="Discovery Life Plan",
        product_type="life",
        provider="Discovery",
        confirmed_by="a-1",
        aliases=["green one"],
        at=AT,
    )
    reloaded = JsonGlossary(path)
    assert reloaded.get("green one").canonical_name == "Discovery Life Plan" and reloaded.entries()[0].confirmed_at == AT
    confirm_term(reloaded, "the green policy", canonical_name="Discovery Life Plan", confirmed_by="a-2", aliases=["the green one"], at=AT)
    assert sorted(JsonGlossary(path).get("the green policy").aliases) == ["green one", "the green one"]
    assert isinstance(g, GlossaryStore) and isinstance(InMemoryGlossary(), GlossaryStore)


# --------------------------------------------------------------------------- detection


def test_is_unknown(sample_fna_json):
    doc = FnaDocument.model_validate(sample_fna_json)
    ctx = TermContext.from_fna(doc, crm_terms=["Acme Holdings Trust"], grammar_products=["Wealth Wrap Plus"])
    assert ctx.prior_fna_terms
    for known in (*ctx.prior_fna_terms[:3], "Acme Holdings Trust", "Wealth Wrap Plus", "Discovery", "living annuity", "TFSA", "Glacier"):
        assert not is_unknown(known, ctx), known
    assert is_unknown("Zorblax Wealth Booster", ctx)
    g = InMemoryGlossary([GlossaryEntry(term="Zorblax Wealth Booster", canonical_name="Zorblax", confirmed_by="a", confirmed_at=AT)])
    assert not is_unknown("Zorblax Wealth Booster", ctx, glossary=g)
    assert not is_unknown("", ctx)
