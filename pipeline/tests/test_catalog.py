"""The slot catalog is generated from the FNA app and must stay in step with it (§4)."""

from __future__ import annotations

from advisor_intel import stats
from advisor_intel.models import fna as F
from advisor_intel.models.dists import Categorical
from advisor_intel.schema import generate
from advisor_intel.schema.slots import SCHEMA_DIR, load_catalog


def test_committed_catalog_is_up_to_date():
    assert (SCHEMA_DIR / "profile_v1.yaml").read_text() == generate.generate(), (
        "profile_v1.yaml is stale: run `python -m advisor_intel.schema.generate`"
    )


def test_every_slot_resolves_to_a_document_field():
    cat = load_catalog()
    doc = F.FnaDocument(
        dependants=[F.Dependant()],
        retirementFunds=[F.RetirementFund()],
        assets=[F.Asset()],
        liabilities=[F.Liability()],
        policies=[F.Policy()],
        goals=[F.OtherGoal()],
        expenses=F.Expenses(custom=[F.CustomExpense()]),
    ).model_dump()
    for s in cat.slots:
        if not s.exported:
            continue
        path = s.fna_path.replace("{p}", "client")
        if path.startswith("riskProfile.answers[") or path == "expenses.items[]":
            continue
        node = doc
        for part in path.split("."):
            if part.endswith("[]"):
                node = node[part[:-2]][0]
            else:
                assert part in node, f"{s.slot_id}: {part} missing"
                node = node[part]


def test_priors_fit_categories_and_bounds():
    cat = load_catalog()
    for s in cat.slots:
        if s.population_prior is None:
            continue
        if isinstance(s.population_prior, Categorical):
            assert set(s.population_prior.probs) <= set(s.categories or []), s.slot_id
        elif s.bounds and s.value_type not in ("category", "boolean", "probability", "count"):
            lo, hi = s.bounds
            med = stats.median(s.population_prior)
            assert lo <= med <= hi, (s.slot_id, med, s.bounds)


def test_tier_a_coverage_and_group_sizes():
    cat = load_catalog()
    tier_a = {s.slot_id for s in cat.tier_a()}
    for must in ("{p}.dateOfBirth", "income.{p}.grossMonthly", "retirement.{p}.retirementAge", "dependants[].tertiaryCostAnnual", "policies[].cover", "estate.{p}.hasWill"):
        assert must in tier_a
    for g in cat.groups:
        assert 1 <= len(cat.in_group(g)) <= 12, g
    assert all(s.question for s in cat.slots)


def test_catalog_tables_come_from_the_app():
    cat = load_catalog()
    assert {e.key for e in cat.expense_items} >= {"rent", "groceries", "school-fees", "family-support", "medical-aid"}
    assert [q.id for q in cat.risk_questions] == ["horizon", "income-stability", "emergency", "proportion", "knowledge", "objective", "drawdown", "tradeoff"]
    horizon = cat.slot("riskProfile.answers[horizon]")
    assert horizon.store_as == "index" and horizon.categories[0] == "Within 1 year"
    assert cat.slot("household.maritalRegime").categories == ["icop", "anc-accrual", "anc-no-accrual", "customary", "foreign", "na"]
    assert cat.slot("retirement.client.retirementAge").fna_default == 65
    assert cat.slot("dependants[].tertiaryStartAge").fna_default == 19


def test_coverage_check_catches_new_app_fields(tmp_path):
    import pytest
    import yaml

    from advisor_intel.config import REPO_DIR
    from advisor_intel.schema.ts_types import read_app_sources

    src = read_app_sources(REPO_DIR)
    src.types.interfaces["Dependant"]["favouriteColour"] = src.types.interfaces["Dependant"]["name"]
    ann = yaml.safe_load(generate.ANNOTATIONS.read_text())
    with pytest.raises(ValueError, match="favouriteColour"):
        generate.build_catalog(src, ann)
