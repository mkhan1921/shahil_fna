"""Claim sentences for the entailment check (spec §9.1a).

Each hypothesis becomes one plain sentence about the slot, the person or object it concerns and the
value: "The client's target retirement age is between 60 and 65." The sentence is built from the slot
and the hypothesis value only, never from the extractor's interpretation, reasoning or self-score
(§9.1e), so the verifier judges what the profile says rather than how the extractor read the call.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from .. import stats
from ..models.dists import Bernoulli, Categorical, Discrete, Dist, LogNormal, Mixture, Normal, Point, Quantity, Triangular
from ..models.facts import Fact
from ..models.profile import Profile
from ..schema.slots import Catalog, SlotDef, load_catalog

MIN_WEIGHT = 0.1  # §9.1a: every hypothesis with weight ≥ 0.1 is checked

# Natural noun phrases, so claims read like what a client would say rather than field names.
NOUNS: dict[str, str] = {
    "{p}.dateOfBirth": "date of birth",
    "{p}.employmentType": "employment type",
    "{p}.occupation": "occupation",
    "{p}.employer": "employer",
    "{p}.healthNotes": "health",
    "{p}.hazardousPursuits": "hazardous pursuits",
    "household.maritalStatus": "marital status",
    "household.maritalRegime": "marital property regime",
    "household.dateOfMarriage": "date of marriage",
    "dependants[].name": "name",
    "dependants[].relationship": "relationship to the client",
    "dependants[].dateOfBirth": "date of birth",
    "dependants[].dependencyEndAge": "age at which they stop being financially dependent",
    "dependants[].monthlySupport": "monthly financial support from the household",
    "dependants[].supportYears": "number of years of support",
    "dependants[].schoolType": "type of school",
    "dependants[].schoolFeesAnnual": "school fees",
    "dependants[].tertiaryStartAge": "age at the start of tertiary study",
    "dependants[].tertiaryYears": "length of tertiary study",
    "dependants[].tertiaryCostAnnual": "tertiary study cost",
    "dependants[].educationSavings": "money already saved for education",
    "dependants[].educationSavingsMonthly": "monthly saving for education",
    "income.{p}.grossMonthly": "gross salary",
    "income.{p}.annualBonus": "annual bonus",
    "income.{p}.otherTaxableMonthly": "other taxable income",
    "income.{p}.nonTaxableMonthly": "non-taxable income",
    "income.{p}.medicalAidPayrollMonthly": "medical aid deduction from salary",
    "income.{p}.medicalSchemeMembers": "number of medical scheme members",
    "income.{p}.otherPayrollDeductionsMonthly": "other payroll deductions",
    "income.{p}.businessGrowthRate": "expected business growth rate",
    "income.{p}.expectedChange[].newGrossMonthly": "new gross salary after the change",
    "income.{p}.expectedChange[].year": "year of the income change",
    "expenses.custom[].amount": "monthly cost",
    "expenses.custom[].category": "expense category",
    "retirementFunds[].type": "fund type",
    "retirementFunds[].owner": "owner",
    "retirementFunds[].provider": "provider",
    "retirementFunds[].value": "current value",
    "retirementFunds[].savingsPot": "two-pot savings pot balance",
    "retirementFunds[].employeeMonthly": "monthly contribution by the member",
    "retirementFunds[].employerMonthly": "monthly contribution by the employer",
    "retirementFunds[].drawdownRate": "drawdown rate",
    "assets[].type": "asset type",
    "assets[].owner": "owner",
    "assets[].value": "value",
    "assets[].earmark": "purpose",
    "assets[].baseCost": "base cost",
    "assets[].monthlyContribution": "monthly contribution",
    "assets[].monthlyIncome": "monthly income it pays",
    "liabilities[].type": "type of debt",
    "liabilities[].owner": "owner",
    "liabilities[].balance": "outstanding balance",
    "liabilities[].monthlyRepayment": "monthly repayment",
    "liabilities[].interestRate": "interest rate",
    "policies[].type": "type of cover",
    "policies[].lifeAssured": "life assured",
    "policies[].insurer": "insurer",
    "policies[].cover": "cover amount",
    "policies[].monthlyBenefit": "monthly benefit",
    "policies[].beneficiary": "beneficiary",
    "policies[].premiumMonthly": "monthly premium",
    "policies[].waitingPeriodMonths": "waiting period",
    "policies[].benefitToAge": "age to which the benefit pays",
    "policies[].benefitTermYears": "benefit term",
    "policies[].definition": "disability definition",
    "policies[].premiumEscalation": "premium escalation",
    "policies[].coverEscalation": "cover escalation",
    "policies[].inceptionDate": "start date",
    "retirement.{p}.retirementAge": "target retirement age",
    "retirement.{p}.planningAge": "age to which retirement income must last",
    "retirement.{p}.targetMode": "way of setting the retirement income goal",
    "retirement.{p}.targetPercent": "target retirement income as a share of current income",
    "retirement.{p}.targetMonthly": "target monthly retirement income in today's money",
    "retirement.{p}.otherIncomeMonthly": "other income in retirement",
    "goals[].name": "name",
    "goals[].cost": "cost in today's money",
    "goals[].targetYear": "target year",
    "goals[].existingSavings": "money already saved for it",
    "goals[].monthlyContribution": "monthly saving towards it",
    "goals[].priority": "priority",
    "goals[].owner": "owner",
    "estate.{p}.hasWill": "will status",
    "estate.{p}.willDate": "will date",
    "estate.{p}.willLocation": "will location",
    "estate.{p}.executor": "executor",
    "estate.{p}.guardianNominated": "guardian nomination for minor children",
    "estate.{p}.trustNotes": "trust",
    "estate.{p}.funeralCost": "expected funeral cost",
    "estate.{p}.additionalIncomeNeedMonthly": "extra monthly income the family would need on death",
    "estate.{p}.additionalCapitalNeed": "extra capital needed on death",
    "estate.{p}.cashBequests": "cash bequests in the will",
    "estate.{p}.accrualCommencementValue": "estate value at the start of the marriage (accrual)",
    "estate.{p}.portedAbatement": "estate duty abatement ported from a late spouse",
    "engagement.reasonForAdvice": "reason for seeking advice",
    "engagement.clientObjectives": "objectives",
    "engagement.clientConcerns": "concerns",
    "engagement.otherAdvisers": "other advisers",
    "event[].kind": "kind of event",
    "event[].year": "expected year",
    "event[].cashImpact": "cash impact",
}

# (true, false) phrasings for booleans; "{s}" is the subject.
BOOLEANS: dict[str, tuple[str, str]] = {
    "{p}.smoker": ("{s} smokes", "{s} does not smoke"),
    "household.includeSpouse": ("the plan includes the client's spouse or partner", "the plan does not include a spouse or partner"),
    "dependants[].specialNeeds": ("{s} has special needs", "{s} does not have special needs"),
    "dependants[].tertiary": ("{s} plans to go on to tertiary study", "{s} does not plan to go on to tertiary study"),
    "income.{p}.uifApplies": ("UIF is deducted from {sp} salary", "UIF is not deducted from {sp} salary"),
    "expenses.custom[].essential": ("{s} is an essential expense", "{s} is not an essential expense"),
    "retirementFunds[].beneficiariesNominated": ("beneficiaries are nominated on {s}", "no beneficiaries are nominated on {s}"),
    "retirementFunds[].viaPayroll": ("contributions to {s} are paid through payroll", "contributions to {s} are not paid through payroll"),
    "assets[].bequeathToSpouse": ("{s} is left to the spouse", "{s} is not left to the spouse"),
    "liabilities[].creditLifeCover": ("{s} has credit life cover", "{s} has no credit life cover"),
    "liabilities[].settleOnDeath": ("{s} should be paid off on death", "{s} need not be paid off on death"),
    "liabilities[].settleOnDisability": ("{s} should be paid off on disability", "{s} need not be paid off on disability"),
    "policies[].isGroup": ("{s} is a group (employer) policy", "{s} is an individual policy, not a group one"),
    "policies[].accelerated": ("{s} is an accelerated benefit", "{s} is a stand-alone benefit"),
    "estate.{p}.hasTrust": ("{s} has a trust", "{s} has no trust"),
}

LIST_NOUN: dict[str, str] = {
    "dependants[]": "dependant",
    "retirementFunds[]": "retirement fund",
    "assets[]": "asset",
    "liabilities[]": "debt",
    "policies[]": "policy",
    "goals[]": "goal",
    "expenses.custom[]": "expense",
    "event[]": "event",
}


@dataclass(frozen=True)
class Claim:
    claim_id: str
    fact_id: str
    hypothesis_index: int
    weight: float
    text: str


# --------------------------------------------------------------------------- subjects


def _list_prefix(slot_id: str) -> str | None:
    if "expectedChange[]" in slot_id:
        return "expectedChange[]"
    for prefix in LIST_NOUN:
        if slot_id.startswith(prefix):
            return prefix
    return None


def subject(fact: Fact, profile: Profile, catalog: Catalog) -> tuple[str, str]:
    """(subject, possessive subject): ("the client", "the client's"), ("Thandi (dependant)", "Thandi's") …"""
    slot = catalog.slot(fact.slot_id)
    if slot.per_person and "expectedChange[]" not in fact.slot_id:
        person = (fact.instance_key or "client").split(".")[-1]
        s = "the client's spouse" if person == "spouse" else "the client"
        return s, s + "'s"
    if fact.slot_id == "expenses.items[]":
        item = catalog.expense_item(fact.instance_key or "")
        label = item.label.lower() if item else (fact.instance_key or "this item")
        return f"the household's {label} expense", f"the household's {label} expense's"
    if fact.slot_id.startswith("riskProfile.answers["):
        return "the client", "the client's"
    prefix = _list_prefix(fact.slot_id)
    if prefix:
        name = ""
        if fact.instance_key and fact.instance_key in profile.entities:
            name = profile.entities[fact.instance_key].canonical_name
        noun = LIST_NOUN.get(prefix, "expected income change")
        if prefix == "expectedChange[]":
            person = "spouse" if ".spouse." in fact.fna_path else "client"
            noun = f"expected change to the {'spouse' if person == 'spouse' else 'client'}'s income"
        s = f"the {noun} '{name}'" if name else f"the {noun}"
        return s, s + "'s"
    return "the household", "the household's"


def noun(slot: SlotDef) -> str:
    return NOUNS.get(slot.slot_id, slot.label.lower() or slot.field)


# --------------------------------------------------------------------------- value phrases


def _fmt(x: float, q: Quantity, slot: SlotDef) -> str:
    if not math.isfinite(x):
        return "an unbounded amount"
    if q.unit in ("ZAR", "USD", "GBP", "EUR"):
        sym = {"ZAR": "R", "USD": "$", "GBP": "£", "EUR": "€"}[q.unit]
        return f"{sym}{x:,.0f}"
    if q.unit == "pct":
        s = f"{x * 100:.2f}".rstrip("0").rstrip(".")
        return f"{s}%"
    if q.unit == "date":
        if slot.value_type == "year":
            return f"{x:.0f}"
        iso = stats.year_to_iso(x)
        return date.fromisoformat(iso).strftime("%B %Y")
    if q.unit == "years":
        return f"{x:g} year" + ("" if abs(x - 1) < 1e-9 else "s")
    if q.unit == "months":
        return f"{x:g} month" + ("" if abs(x - 1) < 1e-9 else "s")
    return f"{x:g}" if abs(x) < 1e6 else f"{x:,.0f}"


def _per(q: Quantity) -> str:
    return {"monthly": " a month", "annual": " a year"}.get(q.period, "")


def numeric_phrase(q: Quantity, slot: SlotDef) -> str:
    """'62', 'between 60 and 65', 'about R2,500,000 (between R2,250,000 and R2,750,000)'."""
    d = q.dist
    per = _per(q)

    def f(x: float) -> str:
        return _fmt(x, q, slot)

    if isinstance(d, Point):
        if isinstance(d.value, str):
            try:
                return f(stats.as_number(d.value)) + per
            except ValueError:
                return d.value
        return f(stats.as_number(d.value)) + per
    if isinstance(d, Triangular):
        if d.high - d.low <= 1e-9:
            return f(d.mode) + per
        mid = (d.low + d.high) / 2
        tail = "" if abs(d.mode - mid) <= 0.05 * (d.high - d.low) else f", most likely {f(d.mode)}"
        return f"between {f(d.low)} and {f(d.high)}{per}{tail}"
    if isinstance(d, Discrete):
        pairs = sorted(zip(d.support, d.weights), key=lambda t: -t[1])
        if len(pairs) == 1:
            return f(pairs[0][0]) + per
        vals = sorted(v for v, _ in pairs)
        if len(vals) <= 4:
            listed = ", ".join(f(v) for v in vals[:-1]) + f" or {f(vals[-1])}"
            return f"{listed}{per}, most likely {f(pairs[0][0])}"
        return f"between {f(vals[0])} and {f(vals[-1])}{per}, most likely {f(pairs[0][0])}"
    if isinstance(d, (LogNormal, Normal, Mixture)):
        s = stats.summary(d)
        if abs(s["p90"] - s["p10"]) <= 1e-9 * max(1.0, abs(s["p50"])):
            return f(s["p50"]) + per
        return f"about {f(s['p50'])}{per} (between {f(s['p10'])} and {f(s['p90'])})"
    return f(stats.median(d)) + per


def _likelihood(p: float) -> str:
    if p >= 0.9:
        return "will happen"
    if p >= 0.65:
        return "will probably happen"
    if p >= 0.35:
        return "might happen"
    if p > 0.1:
        return "is unlikely to happen"
    return "will not happen"


def categorical_phrase(dist: Dist, slot: SlotDef) -> str:
    probs = sorted(stats.categorical_probs(dist).items(), key=lambda kv: -kv[1])
    top, p_top = probs[0]
    if p_top >= 0.9 or len(probs) == 1:
        return f"'{top}'"
    second = probs[1][0]
    return f"probably '{top}' (otherwise '{second}')"


# Categorical answers that read better as sentences; "{s}" is the subject.
CATEGORY_SENTENCES: dict[str, dict[str, str]] = {
    "estate.{p}.hasWill": {
        "yes": "{s} has a will",
        "no": "{s} does not have a will",
        "unsure": "{s} is not sure whether they have a valid will",
    },
    "estate.{p}.guardianNominated": {
        "yes": "{s} has nominated a guardian for their minor children",
        "no": "{s} has not nominated a guardian for their minor children",
        "na": "a guardian nomination does not apply to {s}",
    },
}


def _cap(text: str) -> str:
    return text[0].upper() + text[1:] if text else text


def _is_value(phrase: str) -> str:
    """'is between 60 and 65' — numeric phrases already carry their own qualifiers."""
    return f"is {phrase}"


def _date_phrase(q: Quantity, slot: SlotDef) -> str:
    d = q.dist
    if isinstance(d, Point):
        return numeric_phrase(q, slot)
    s = stats.summary(d)
    if s["p90"] - s["p10"] <= 1.1:  # a stated age gives a date of birth ±6 months (§7.6)
        return f"around {_fmt(s['p50'], q, slot)}"
    return f"between {_fmt(s['p10'], q, slot)} and {_fmt(s['p90'], q, slot)}"


def _value_sentence(fact: Fact, q: Quantity, slot: SlotDef, s: str, sp: str, anchor: date) -> str:
    d = q.dist
    n = noun(slot)
    listed = _list_prefix(fact.slot_id) is not None

    def about(predicate: str) -> str:
        # "The client's gross salary is R45,000 a month." / "For the policy 'X', the cover amount is …"
        return f"For {s}, the {n} {predicate}." if listed else f"{_cap(sp)} {n} {predicate}."

    if slot.value_type == "text":
        text = d.value if isinstance(d, Point) else stats.mode_label(d)
        return about(f'is described as "{text}"')
    if slot.value_type == "probability" or fact.slot_id.endswith(".occurs"):
        return f"{_cap(s)} {_likelihood(stats.p_true(d))}."
    if slot.value_type == "boolean" or isinstance(d, Bernoulli) or (isinstance(d, Point) and isinstance(d.value, bool)):
        p = stats.p_true(d)
        yes, no = BOOLEANS.get(slot.slot_id, ("for {s}, '{n}' is yes", "for {s}, '{n}' is no"))
        sent = (yes if p >= 0.5 else no).format(s=s, sp=sp, n=n)
        if 0.1 < p < 0.9:
            sent = f"probably {sent}"
        return _cap(sent) + "."
    if slot.value_type in ("category", "count") or isinstance(d, Categorical) or stats.is_categorical(d):
        phrases = CATEGORY_SENTENCES.get(slot.slot_id)
        probs = sorted(stats.categorical_probs(d).items(), key=lambda kv: -kv[1])
        if phrases and probs[0][0] in phrases:
            sent = phrases[probs[0][0]].format(s=s)
            return _cap(("probably " if probs[0][1] < 0.9 else "") + sent) + "."
        return about(f"is {categorical_phrase(d, slot)}")
    if fact.slot_id == "expenses.items[]":
        label = s.removeprefix("the household's ").removesuffix(" expense")
        return f"The household spends {numeric_phrase(q, slot)} on {label}."
    if slot.value_type == "date" and q.unit == "date":
        phrase = _date_phrase(q, slot)
        if "dateOfBirth" in slot.slot_id:
            age = stats.iso_to_year(anchor.isoformat()) - stats.median(d)
            return about(f"is {phrase} (about {age:.0f} years old at the call)")
        return about(f"is {phrase}")
    return about(_is_value(numeric_phrase(q, slot)))


def claim_text(fact: Fact, value: Quantity, *, profile: Profile, catalog: Catalog | None = None) -> str:
    """The claim sentence for one value of a fact."""
    catalog = catalog or load_catalog(profile.schema_version)
    slot = catalog.slot(fact.slot_id)
    s, sp = subject(fact, profile, catalog)
    if fact.slot_id.startswith("riskProfile.answers["):
        qid = fact.slot_id[len("riskProfile.answers[") : -1]
        rq = next((r for r in catalog.risk_questions if r.id == qid), None)
        asked = f"'{rq.question}'" if rq else f"'{qid}'"
        return f"The client's answer to the risk question {asked} is {categorical_phrase(value.dist, slot)}."
    return _value_sentence(fact, value, slot, s, sp, profile.anchor_date)


def claims_for_fact(fact: Fact, *, profile: Profile, catalog: Catalog | None = None, min_weight: float = MIN_WEIGHT) -> list[Claim]:
    """One claim per hypothesis with weight ≥ ``min_weight`` (§9.1a)."""
    out = []
    for i, h in enumerate(fact.hypotheses):
        if h.value is None or h.weight < min_weight - 1e-12:
            continue
        out.append(
            Claim(
                claim_id=f"c{i}",
                fact_id=fact.fact_id,
                hypothesis_index=i,
                weight=h.weight,
                text=claim_text(fact, h.value, profile=profile, catalog=catalog),
            )
        )
    return out
