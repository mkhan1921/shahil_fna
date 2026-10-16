"""Constraint rules over the whole profile (spec §9.1c), also used for scale resolution (§7.4 signal 4).

Rules are declared once in ``RULES``, each with a kind (range, ordering, budget, coherence, cross-slot),
a severity and a weight:

- a **hard** violation sets ``verification["constraint"] = "fail"`` on the facts it is pinned on, which
  forces review (§10.3) and blocks auto-accept (§9.5);
- a **soft** violation is a calibration feature (``constraint_soft_violations``, §9.2) and down-weights a
  scale reading that would break it (``soft_penalty``, §7.4).

Rules read the profile through ``ProfileView``: each fact at its median (numbers and dates as decimal
years), mode (categories) or p ≥ 0.5 (booleans), with optional per-fact overrides so scale resolution can
ask "what if this amount were R250,000 instead of R2,500,000?". Rejected facts and facts without a value
are invisible to the rules: a missing field is the omission check's job (§9.1d), not a violation.

Budget rules compare against the household's monthly net income when the caller knows it (the FNA
engine's take-home); otherwise they use gross income, an upper bound on net, so a breach against gross
is a breach against net too. Nothing here computes tax (§12.7: one tax implementation).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Literal, Mapping

from pydantic import BaseModel

from .. import stats
from ..models.dists import Quantity
from ..models.profile import Profile
from .slots import Catalog, fact_id_for, load_catalog

Severity = Literal["hard", "soft"]
RuleKind = Literal["range", "ordering", "budget", "coherence", "cross_slot"]
Status = Literal["pass", "soft", "fail"]

SEED_SOURCES = frozenset({"prior_fna", "crm", "fna_default"})
HARD_PENALTY_FLOOR = 0.05  # a reading that breaks a hard rule keeps 5%, so a span whose readings all break one still resolves
BUDGET_RATIO = 1.5  # §9.1c: monthly core expenses ≤ 1.5 × monthly net income
PRIOR_SCALE_RATIO = 10.0  # §7.4: a value 10× (or a tenth of) the prior FNA value with no reason given
MIN_FUND_YEARS = 2.0  # §9.1c: balance > 0 if contributions > 0 for more than 2 years
BUSINESS_ASSET_TYPES = frozenset({"business-interest", "loan-account"})
NOT_WORKING = frozenset({"unemployed", "student", "retired"})
UNPARTNERED = frozenset({"single", "divorced", "widowed"})


class Violation(BaseModel):
    """One rule broken by one set of facts."""

    rule_id: str
    kind: RuleKind
    severity: Severity
    weight: float
    fact_ids: list[str]  # every fact the rule read for this instance
    blamed: list[str]  # the facts a failure is pinned on: call facts first, seeds only when nothing else is involved
    message: str


@dataclass(frozen=True)
class Rule:
    rule_id: str
    kind: RuleKind
    severity: Severity
    weight: float  # soft: likelihood down-weight for scale resolution (§7.4); hard rules use 1.0
    description: str
    check: Callable[["ProfileView"], Iterable[tuple[list[str], str]]]


# --------------------------------------------------------------------------- the profile as rules see it


def _who(person: str | None) -> str:
    return {"client": "the client", "spouse": "the spouse"}.get(person or "", person or "the household")


def _fmt_money(x: float) -> str:
    return f"R{x:,.0f}"


class ProfileView:
    """Point values of a profile's facts for rule checks, with optional per-fact overrides."""

    def __init__(
        self,
        profile: Profile,
        catalog: Catalog | None = None,
        overrides: Mapping[str, Any] | None = None,
        *,
        net_income_monthly: float | None = None,
    ) -> None:
        self.profile = profile
        self.catalog = catalog or load_catalog(profile.schema_version)
        self.overrides = dict(overrides or {})
        self.net_income_monthly = net_income_monthly
        self.anchor_year = stats.iso_to_year(profile.anchor_date.isoformat())
        self._by_slot: dict[str, list[str]] = {}
        for fid, f in profile.facts.items():
            self._by_slot.setdefault(f.slot_id, []).append(fid)
        for fid in self.overrides:
            slot_id = fid.split("#", 1)[0]
            if fid not in profile.facts:
                self._by_slot.setdefault(slot_id, []).append(fid)

    # -- lookups

    def fact_ids(self, slot_id: str) -> list[str]:
        return list(self._by_slot.get(slot_id, []))

    def entities(self, list_prefix: str) -> list[str]:
        """Instance keys of every fact under a list (``'dependants[]'``), in first-seen order."""
        seen: dict[str, None] = {}
        for slot_id, fids in self._by_slot.items():
            if slot_id.startswith(list_prefix):
                for fid in fids:
                    inst = fid.split("#", 1)[1] if "#" in fid else None
                    if inst:
                        seen.setdefault(inst, None)
        return list(seen)

    def persons(self) -> list[str]:
        out = ["client"]
        has_spouse = "person.spouse" in self.profile.entities or self.flag("household.includeSpouse") is True
        has_spouse = has_spouse or any(fid.endswith("#spouse") for fid in self.profile.facts)
        return out + (["spouse"] if has_spouse else [])

    def raw(self, fact_id: str) -> Any:
        if fact_id in self.overrides:
            v = self.overrides[fact_id]
            return v.dist if isinstance(v, Quantity) else v
        f = self.profile.facts.get(fact_id)
        if f is None or f.value is None or f.review.status == "rejected":
            return None
        return f.value.dist

    def num(self, fact_id: str) -> float | None:
        v = self.raw(fact_id)
        if v is None:
            return None
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return stats.as_number(v)
            except ValueError:
                return None
        try:
            if stats.is_boolean(v):
                return stats.p_true(v)
            if stats.is_categorical(v):
                return float(stats.mode_label(v))
            x = stats.median(v)
        except (TypeError, ValueError):
            return None
        return x if math.isfinite(x) else None

    def label(self, fact_id: str) -> str | None:
        v = self.raw(fact_id)
        if v is None:
            return None
        if isinstance(v, (bool, int, float, str)):
            return str(v)
        try:
            return stats.mode_label(v)
        except TypeError:
            return None

    def flag(self, fact_id: str) -> bool | None:
        v = self.raw(fact_id)
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v >= 0.5
        if isinstance(v, str):
            return v.strip().lower() in ("true", "yes", "1")
        try:
            return stats.p_true(v) >= 0.5
        except TypeError:
            return None

    # -- derived quantities

    def person_age(self, person: str) -> float | None:
        dob = self.num(fact_id_for("{p}.dateOfBirth", person))
        return None if dob is None else self.anchor_year - dob

    def dependant_age(self, entity_id: str) -> float | None:
        dob = self.num(fact_id_for("dependants[].dateOfBirth", entity_id))
        return None if dob is None else self.anchor_year - dob

    def household_income(self) -> tuple[float, str, list[str]] | None:
        """Monthly household income: net when the caller knows it, else gross (an upper bound on net)."""
        ids: list[str] = []
        total = 0.0
        for p in self.persons():
            for slot_id, factor in (
                ("income.{p}.grossMonthly", 1.0),
                ("income.{p}.otherTaxableMonthly", 1.0),
                ("income.{p}.nonTaxableMonthly", 1.0),
                ("income.{p}.annualBonus", 1 / 12),
            ):
                fid = fact_id_for(slot_id, p)
                x = self.num(fid)
                if x is not None and x > 0:
                    total += x * factor
                    ids.append(fid)
        if self.net_income_monthly is not None and self.net_income_monthly > 0:
            return self.net_income_monthly, "net", ids
        if total <= 0:
            return None
        return total, "gross", ids

    def fund_age_years(self, entity_id: str) -> float | None:
        """How long a fund has existed, from registry attributes (CRM or discourse), when known."""
        e = self.profile.entities.get(entity_id)
        if e is None:
            return None
        attrs = e.attributes
        for key in ("contribution_years", "years_contributing", "fund_age_years"):
            v = attrs.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        for key in ("start_year", "inception_year", "start_date", "inception_date", "opened", "since"):
            v = attrs.get(key)
            if v in (None, ""):
                continue
            try:
                start = float(v) if isinstance(v, (int, float)) else stats.iso_to_year(str(v))
            except ValueError:
                continue
            return self.anchor_year - start
        return None


# --------------------------------------------------------------------------- rule checks

Found = Iterator[tuple[list[str], str]]


def _retirement_age_vs_current_age(v: ProfileView) -> Found:
    for p in v.persons():
        ra_id = fact_id_for("retirement.{p}.retirementAge", p)
        ra, age = v.num(ra_id), v.person_age(p)
        if ra is None or age is None or v.label(fact_id_for("{p}.employmentType", p)) == "retired":
            continue
        if ra < age - 0.5:
            yield [ra_id, fact_id_for("{p}.dateOfBirth", p)], f"retirement age {ra:g} for {_who(p)} is below their current age ({age:.0f})"


def _person_age_plausible(v: ProfileView) -> Found:
    for p in v.persons():
        age = v.person_age(p)
        if age is not None and not (16 <= age <= 110):
            yield [fact_id_for("{p}.dateOfBirth", p)], f"date of birth gives {_who(p)} an age of {age:.0f}"


def _planning_after_retirement(v: ProfileView) -> Found:
    for p in v.persons():
        ra_id, pa_id = fact_id_for("retirement.{p}.retirementAge", p), fact_id_for("retirement.{p}.planningAge", p)
        ra, pa = v.num(ra_id), v.num(pa_id)
        if ra is not None and pa is not None and pa <= ra:
            yield [pa_id, ra_id], f"planning age {pa:g} for {_who(p)} is not after the retirement age {ra:g}"


def _dependant_born_before_call(v: ProfileView) -> Found:
    for eid in v.entities("dependants[]"):
        fid = fact_id_for("dependants[].dateOfBirth", eid)
        dob = v.num(fid)
        if dob is not None and dob > v.anchor_year + 1.0:  # an expected birth within a year is allowed
            yield [fid], f"dependant {eid} is born after the call ({dob:.1f})"


def _goal_year_not_past(v: ProfileView) -> Found:
    for eid in v.entities("goals[]"):
        fid = fact_id_for("goals[].targetYear", eid)
        y = v.num(fid)
        if y is not None and y < math.floor(v.anchor_year):
            yield [fid], f"goal {eid} has a target year ({y:.0f}) before the call"


def _will_date_not_future(v: ProfileView) -> Found:
    for p in v.persons():
        fid = fact_id_for("estate.{p}.willDate", p)
        y = v.num(fid)
        if y is not None and y > v.anchor_year + 1 / 365:
            yield [fid], f"the will of {_who(p)} is dated after the call"


def _savings_pot_within_fund(v: ProfileView) -> Found:
    for eid in v.entities("retirementFunds[]"):
        pot_id, val_id = fact_id_for("retirementFunds[].savingsPot", eid), fact_id_for("retirementFunds[].value", eid)
        pot, val = v.num(pot_id), v.num(val_id)
        if pot is not None and val is not None and pot > val * 1.01 + 1:
            yield [pot_id, val_id], f"savings pot {_fmt_money(pot)} exceeds the fund value {_fmt_money(val)}"


def _education_start_after_now(v: ProfileView) -> Found:
    for eid in v.entities("dependants[]"):
        if v.label(fact_id_for("dependants[].relationship", eid)) not in (None, "child"):
            continue
        age = v.dependant_age(eid)
        if age is None or age >= 18 or v.flag(fact_id_for("dependants[].tertiary", eid)) is False:
            continue
        start_id = fact_id_for("dependants[].tertiaryStartAge", eid)
        start = v.num(start_id)
        if start is not None and start < age - 0.5:
            yield (
                [start_id, fact_id_for("dependants[].dateOfBirth", eid)],
                (f"tertiary study for {eid} would start at {start:g}, before the call (the child is {age:.0f})"),
            )


def _dependency_end_after_now(v: ProfileView) -> Found:
    for eid in v.entities("dependants[]"):
        if v.label(fact_id_for("dependants[].relationship", eid)) not in (None, "child"):
            continue
        age = v.dependant_age(eid)
        end_id = fact_id_for("dependants[].dependencyEndAge", eid)
        end = v.num(end_id)
        if age is not None and end is not None and age < 18 and end < age:
            yield [end_id, fact_id_for("dependants[].dateOfBirth", eid)], f"dependency ends at {end:g} but {eid} is already {age:.0f}"


def _benefit_to_age_after_now(v: ProfileView) -> Found:
    for eid in v.entities("policies[]"):
        if v.label(fact_id_for("policies[].type", eid)) != "income-protection":
            continue
        person = v.label(fact_id_for("policies[].lifeAssured", eid)) or "client"
        age = v.person_age(person) if person in ("client", "spouse") else None
        fid = fact_id_for("policies[].benefitToAge", eid)
        to_age = v.num(fid)
        if age is not None and to_age is not None and to_age <= age:
            yield [fid], f"income protection {eid} pays to age {to_age:g}, but {_who(person)} is already {age:.0f}"


def _expense_facts(v: ProfileView) -> list[tuple[str, float, bool]]:
    """(fact id, monthly amount, essential) for every expense line with a value."""
    out = []
    for fid in v.fact_ids("expenses.items[]"):
        x = v.num(fid)
        key = fid.split("#", 1)[1] if "#" in fid else ""
        item = v.catalog.expense_item(key)
        if x is not None and x > 0:
            out.append((fid, x, bool(item and item.essential)))
    for eid in v.entities("expenses.custom[]"):
        fid = fact_id_for("expenses.custom[].amount", eid)
        x = v.num(fid)
        if x is not None and x > 0:
            out.append((fid, x, bool(v.flag(fact_id_for("expenses.custom[].essential", eid)))))
    return out


def _core_expenses_vs_income(v: ProfileView) -> Found:
    inc = v.household_income()
    if inc is None:
        return
    income, basis, income_ids = inc
    core = [(fid, x) for fid, x, essential in _expense_facts(v) if essential]
    total = sum(x for _, x in core)
    if core and total > BUDGET_RATIO * income:
        yield (
            [fid for fid, _ in core] + income_ids,
            (f"core expenses {_fmt_money(total)} a month exceed {BUDGET_RATIO:g}× the household's {basis} income ({_fmt_money(income)})"),
        )


def _expense_line_vs_income(v: ProfileView) -> Found:
    inc = v.household_income()
    if inc is None:
        return
    income, basis, income_ids = inc
    for fid, x, _ in _expense_facts(v):
        if x > income:
            yield (
                [fid] + income_ids,
                f"one expense line ({_fmt_money(x)} a month) exceeds the household's {basis} income ({_fmt_money(income)})",
            )


def _repayments_vs_income(v: ProfileView) -> Found:
    inc = v.household_income()
    if inc is None:
        return
    income, basis, income_ids = inc
    reps = [
        (fid, x) for eid in v.entities("liabilities[]") for fid in [fact_id_for("liabilities[].monthlyRepayment", eid)] if (x := v.num(fid))
    ]
    total = sum(x for _, x in reps)
    if reps and total > income:
        yield [fid for fid, _ in reps] + income_ids, f"debt repayments {_fmt_money(total)} a month exceed the household's {basis} income"


def _contributions_vs_income(v: ProfileView) -> Found:
    for eid in v.entities("retirementFunds[]"):
        owner = v.label(fact_id_for("retirementFunds[].owner", eid)) or "client"
        if owner not in ("client", "spouse"):
            continue
        gross_id = fact_id_for("income.{p}.grossMonthly", owner)
        gross = v.num(gross_id)
        ids = [fact_id_for("retirementFunds[].employeeMonthly", eid), fact_id_for("retirementFunds[].employerMonthly", eid)]
        contrib = sum(x for fid in ids if (x := v.num(fid)))
        if gross and contrib > gross:
            yield (
                [fid for fid in ids if v.num(fid)] + [gross_id],
                f"contributions to {eid} ({_fmt_money(contrib)}) exceed {_who(owner)}'s gross income",
            )


def _has_business(v: ProfileView) -> bool:
    if any(e.type == "business" for e in v.profile.entities.values()):
        return True
    return any(v.label(fact_id_for("assets[].type", eid)) in BUSINESS_ASSET_TYPES for eid in v.entities("assets[]"))


def _self_employed_without_business(v: ProfileView) -> Found:
    if _has_business(v):
        return
    for p in v.persons():
        fid = fact_id_for("{p}.employmentType", p)
        if v.label(fid) == "self-employed":
            yield [fid], f"{_who(p)} is self-employed but no business entity was found"


def _spouse_without_partner(v: ProfileView) -> Found:
    inc, ms = "household.includeSpouse", "household.maritalStatus"
    if v.flag(inc) is True and v.label(ms) in UNPARTNERED:
        yield [inc, ms], f"the plan includes a spouse but the marital status is {v.label(ms)}"


def _regime_without_marriage(v: ProfileView) -> Found:
    reg, ms = "household.maritalRegime", "household.maritalStatus"
    regime, status = v.label(reg), v.label(ms)
    if regime not in (None, "", "na") and status not in (None, "married"):
        yield [reg, ms], f"a marital regime ({regime}) is recorded but the marital status is {status}"


def _spouse_items_without_spouse(v: ProfileView) -> Found:
    if "spouse" in v.persons():
        return
    for eid in v.entities("policies[]"):
        fid = fact_id_for("policies[].lifeAssured", eid)
        if v.label(fid) == "spouse":
            yield [fid], f"policy {eid} insures a spouse, but the household has none"
    for eid in v.entities("retirementFunds[]"):
        fid = fact_id_for("retirementFunds[].owner", eid)
        if v.label(fid) == "spouse":
            yield [fid], f"fund {eid} belongs to a spouse, but the household has none"


def _not_working_with_employer_contributions(v: ProfileView) -> Found:
    for eid in v.entities("retirementFunds[]"):
        owner = v.label(fact_id_for("retirementFunds[].owner", eid)) or "client"
        emp_id = fact_id_for("{p}.employmentType", owner) if owner in ("client", "spouse") else None
        er_id = fact_id_for("retirementFunds[].employerMonthly", eid)
        er = v.num(er_id)
        if emp_id and er and er > 0 and v.label(emp_id) in NOT_WORKING:
            yield [er_id, emp_id], f"{_who(owner)} is {v.label(emp_id)} but an employer contributes to {eid}"


def _fund_rows(v: ProfileView) -> Iterator[tuple[str, str, float, float, list[str]]]:
    """(entity, value fact, value, contributions, contribution facts) for funds with contributions and no balance."""
    for eid in v.entities("retirementFunds[]"):
        ids = [fact_id_for("retirementFunds[].employeeMonthly", eid), fact_id_for("retirementFunds[].employerMonthly", eid)]
        contrib = sum(x for fid in ids if (x := v.num(fid)))
        val_id = fact_id_for("retirementFunds[].value", eid)
        val = v.num(val_id)
        if contrib > 0 and val is not None and val <= 0:
            yield eid, val_id, val, contrib, [fid for fid in ids if v.num(fid)]


def _fund_balance_after_contributions(v: ProfileView) -> Found:
    for eid, val_id, _, contrib, ids in _fund_rows(v):
        years = v.fund_age_years(eid)
        if years is not None and years > MIN_FUND_YEARS:
            yield [val_id] + ids, f"fund {eid} has no balance after {years:.0f} years of contributions ({_fmt_money(contrib)} a month)"


def _fund_balance_zero_with_contributions(v: ProfileView) -> Found:
    for eid, val_id, _, contrib, ids in _fund_rows(v):
        if v.fund_age_years(eid) is None:
            yield [val_id] + ids, f"fund {eid} has contributions ({_fmt_money(contrib)} a month) but no balance"


def _amount_vs_prior_scale(v: ProfileView) -> Found:
    for fid, f in v.profile.facts.items():
        if f.prior_value is None:
            continue
        try:
            slot = v.catalog.slot(f.slot_id)
        except KeyError:
            continue
        if slot.value_type != "amount":
            continue
        try:
            prior = stats.median(f.prior_value.dist)
        except TypeError:
            continue
        cur = v.num(fid)
        if cur is None or cur <= 0 or prior <= 0:
            continue
        ratio = cur / prior
        if ratio >= PRIOR_SCALE_RATIO or ratio <= 1 / PRIOR_SCALE_RATIO:
            yield [fid], f"{_fmt_money(cur)} is {ratio:.3g}× the prior FNA value ({_fmt_money(prior)})"


RULES: tuple[Rule, ...] = (
    # range
    Rule(
        "retirement_age_vs_current_age",
        "range",
        "hard",
        1.0,
        "Retirement age ≥ current age (unless retired)",
        _retirement_age_vs_current_age,
    ),
    Rule("person_age_plausible", "range", "hard", 1.0, "A date of birth gives an age between 16 and 110", _person_age_plausible),
    Rule(
        "dependant_born_before_call",
        "range",
        "hard",
        1.0,
        "A dependant is born before the call (or within a year of it)",
        _dependant_born_before_call,
    ),
    Rule("goal_year_not_past", "range", "hard", 1.0, "A goal's target year is not before the call", _goal_year_not_past),
    Rule("will_date_not_future", "range", "hard", 1.0, "A will is not dated after the call", _will_date_not_future),
    Rule("savings_pot_within_fund", "range", "hard", 1.0, "A two-pot savings pot is part of its fund's value", _savings_pot_within_fund),
    # ordering
    Rule("planning_age_after_retirement", "ordering", "hard", 1.0, "Planning age > retirement age", _planning_after_retirement),
    Rule(
        "education_start_after_now",
        "ordering",
        "hard",
        1.0,
        "Education start year ≥ anchor year when the child is under 18",
        _education_start_after_now,
    ),
    Rule(
        "dependency_end_after_now",
        "ordering",
        "soft",
        0.3,
        "A child's dependency does not end before their current age",
        _dependency_end_after_now,
    ),
    Rule(
        "benefit_to_age_after_now",
        "ordering",
        "soft",
        0.3,
        "Income protection pays to an age above the life assured's current age",
        _benefit_to_age_after_now,
    ),
    # budget
    Rule("core_expenses_vs_income", "budget", "soft", 0.5, "Monthly core expenses ≤ 1.5 × monthly net income", _core_expenses_vs_income),
    Rule(
        "expense_line_vs_income", "budget", "soft", 0.5, "No single monthly expense exceeds total monthly income", _expense_line_vs_income
    ),
    Rule("repayments_vs_income", "budget", "soft", 0.3, "Debt repayments do not exceed monthly income", _repayments_vs_income),
    Rule(
        "contributions_vs_income",
        "budget",
        "soft",
        0.5,
        "Retirement contributions do not exceed the owner's gross income",
        _contributions_vs_income,
    ),
    # coherence
    Rule(
        "self_employed_without_business", "coherence", "soft", 0.3, "Self-employed with no business entity", _self_employed_without_business
    ),
    Rule(
        "spouse_without_partner",
        "coherence",
        "soft",
        0.5,
        "A spouse is included but the client is single, divorced or widowed",
        _spouse_without_partner,
    ),
    Rule(
        "regime_without_marriage",
        "coherence",
        "soft",
        0.5,
        "A marital regime is recorded for someone not married",
        _regime_without_marriage,
    ),
    Rule(
        "spouse_items_without_spouse",
        "coherence",
        "soft",
        0.5,
        "A spouse owns or is insured by an item, but there is no spouse",
        _spouse_items_without_spouse,
    ),
    Rule(
        "not_working_with_employer_contributions",
        "coherence",
        "soft",
        0.3,
        "An employer contributes for someone not working",
        _not_working_with_employer_contributions,
    ),
    # cross-slot
    Rule(
        "fund_balance_after_contributions",
        "cross_slot",
        "hard",
        1.0,
        "Fund balance > 0 if contributions > 0 for more than 2 years",
        _fund_balance_after_contributions,
    ),
    Rule(
        "fund_balance_zero_with_contributions",
        "cross_slot",
        "soft",
        0.3,
        "Fund balance > 0 when contributions are paid (fund age unknown)",
        _fund_balance_zero_with_contributions,
    ),
    Rule("amount_vs_prior_scale", "cross_slot", "soft", 0.5, "An amount 10× (or a tenth of) the prior FNA value", _amount_vs_prior_scale),
)
RULE_INDEX: dict[str, Rule] = {r.rule_id: r for r in RULES}


# --------------------------------------------------------------------------- checking and annotating


def _blame(profile: Profile, fact_ids: list[str]) -> list[str]:
    present = [fid for fid in fact_ids if fid in profile.facts]
    call = [fid for fid in present if profile.facts[fid].source not in SEED_SOURCES]
    return call or present or list(fact_ids)


def check_constraints(
    profile: Profile,
    catalog: Catalog | None = None,
    *,
    rules: Iterable[Rule] = RULES,
    net_income_monthly: float | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> list[Violation]:
    """Every rule over the whole profile (§9.1c). Pure: the profile is not changed."""
    view = ProfileView(profile, catalog, overrides, net_income_monthly=net_income_monthly)
    out: list[Violation] = []
    for rule in rules:
        for fids, message in rule.check(view):
            fids = list(dict.fromkeys(fids))
            out.append(
                Violation(
                    rule_id=rule.rule_id,
                    kind=rule.kind,
                    severity=rule.severity,
                    weight=rule.weight,
                    fact_ids=fids,
                    blamed=_blame(profile, fids) if rule.severity == "hard" else fids,
                    message=message,
                )
            )
    return out


def constraint_status(fact_id: str, violations: Iterable[Violation]) -> dict[str, Any]:
    """The ``verification`` fields one fact gets from a set of violations."""
    hard = sorted({v.rule_id for v in violations if v.severity == "hard" and fact_id in v.blamed})
    soft = [v for v in violations if v.severity == "soft" and fact_id in v.fact_ids]
    involved = sorted({v.rule_id for v in violations if fact_id in v.fact_ids})
    status: Status = "fail" if hard else ("soft" if soft else "pass")
    return {
        "constraint": status,
        "constraint_hard": hard,
        "constraint_violations": involved,
        "constraint_soft_violations": len(soft),
        "constraint_messages": [v.message for v in violations if fact_id in v.fact_ids][:5],
    }


def annotate_constraints(profile: Profile, violations: list[Violation]) -> Profile:
    """Write each fact's constraint outcome into ``verification`` (through ``reducer.annotate``)."""
    from ..extract.reducer import annotate  # local import: the reducer imports the models this module reads

    for fid, f in profile.facts.items():
        fields = constraint_status(fid, violations)
        if all(f.verification.get(k) == v for k, v in fields.items()):
            continue
        profile = annotate(profile, fid, verification={**f.verification, **fields})
    return profile


def soft_penalty(
    profile: Profile,
    fact_id: str,
    candidate_value: Any,
    *,
    catalog: Catalog | None = None,
    net_income_monthly: float | None = None,
) -> float:
    """Likelihood factor in (0, 1] for one candidate reading of a fact (§7.4 signal 4).

    The profile is checked with ``fact_id`` set to ``candidate_value`` (a number in the slot's FnaDocument
    unit, a label, a bool, or a ``Quantity`` / distribution). Every rule the fact takes part in and that
    breaks multiplies the factor by ``1 − weight``; hard rules keep ``HARD_PENALTY_FLOOR``. Rules that
    break whatever the reading is scale every reading alike, so they cancel when readings are renormalised.
    """
    view = ProfileView(profile, catalog, {fact_id: candidate_value}, net_income_monthly=net_income_monthly)
    factor = 1.0
    for rule in RULES:
        for fids, _ in rule.check(view):
            if fact_id in fids:
                factor *= 1.0 - min(rule.weight, 1.0 - HARD_PENALTY_FLOOR)
    return factor
