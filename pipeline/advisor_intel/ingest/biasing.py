"""Per-call amount biasing and the custom vocabulary (§5.3, §5.4).

Before decoding, the pipeline builds a phrase list from the client's prior FnaDocument: every amount, age,
policy and provider name in it, with each amount rendered in its spoken forms ("one point two million",
"one comma two mil", "twelve hundred thousand"), plus distractors at the neighbouring scales (×10 and
×0.1) so the boost favours the client's known figures without pulling a new figure onto the wrong scale.
The static SA finance vocabulary (``vocab/za_finance.txt``) and the CRM names complete the list. Each
adapter hands it to its provider's keyterm or custom-vocabulary parameter, in priority order (names and
products, then known amounts, ages, the static list, distractors) within the provider's limit.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

from ..models.fna import FnaDocument
from .grammars import ProductName, age_spoken_forms, amount_spoken_forms, normalize_text, provider_for
from .grammars.catalog import PRODUCTS
from .types import BiasPhrase, CrmNames, PhraseKind, Vocabulary

VOCAB_FILE = Path(__file__).parent / "vocab" / "za_finance.txt"
BOOST = {"name": 2.0, "product": 2.0, "amount": 1.5, "age": 1.2, "vocab": 1.0, "distractor": 0.6}
FORMS_PER_AMOUNT = 5
FORMS_PER_DISTRACTOR = 2
MIN_AMOUNT = 100.0
GENERIC_NAMES = frozenset(
    {"bank", "fund", "provider", "platform", "lisp platform", "money market", "employer", "insurer", "other", "n/a", "na"}
)
_CATALOG_SURFACES = frozenset(s for p in PRODUCTS for s in p.surfaces)

# FnaDocument money fields per list row / person, as (container, fields). Assumptions and the advice record are
# set by the adviser in the app, not said on the call, so they are not boosted.
_PERSON_MONEY = {
    "income": (
        "grossMonthly",
        "annualBonus",
        "otherTaxableMonthly",
        "nonTaxableMonthly",
        "medicalAidPayrollMonthly",
        "otherPayrollDeductionsMonthly",
    ),
    "retirement": ("targetMonthly", "otherIncomeMonthly"),
    "estate": (
        "additionalIncomeNeedMonthly",
        "additionalCapitalNeed",
        "portedAbatement",
        "cashBequests",
        "accrualCommencementValue",
        "funeralCost",
    ),
}
_LIST_MONEY = {
    "dependants": ("monthlySupport", "schoolFeesAnnual", "tertiaryCostAnnual", "educationSavings", "educationSavingsMonthly"),
    "retirementFunds": ("value", "savingsPot", "employeeMonthly", "employerMonthly"),
    "assets": ("value", "baseCost", "monthlyContribution", "monthlyIncome"),
    "liabilities": ("balance", "monthlyRepayment"),
    "policies": ("cover", "monthlyBenefit", "premiumMonthly"),
    "goals": ("cost", "existingSavings", "monthlyContribution"),
}


@lru_cache(maxsize=4)
def load_vocab_file(path: Path = VOCAB_FILE) -> Vocabulary:
    """The static vocabulary: ``term | sounds like …`` lines; ``[section]`` lines are recorded as the phrase source."""
    phrases: list[BiasPhrase] = []
    section = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        term, *sounds = [p.strip() for p in line.split("|")]
        phrases.append(
            BiasPhrase(text=term, kind="vocab", boost=BOOST["vocab"], sounds_like=[s for s in sounds if s], source=f"{path.name}:{section}")
        )
    return Vocabulary(phrases=phrases)


def prior_amounts(doc: FnaDocument) -> list[tuple[str, float]]:
    """(FnaDocument path, value) for every non-zero money field the client could mention."""
    out: list[tuple[str, float]] = []
    for container, fields in _PERSON_MONEY.items():
        for person, block in getattr(doc, container).items():
            for f in fields:
                v = float(getattr(block, f, 0) or 0)
                if v:
                    out.append((f"{container}.{person}.{f}", v))
    for key, v in doc.expenses.items.items():
        if v:
            out.append((f"expenses.items.{key}", float(v)))
    for row in doc.expenses.custom:
        if row.amount:
            out.append((f"expenses.custom[{row.id}].amount", float(row.amount)))
    for lst, fields in _LIST_MONEY.items():
        for row in getattr(doc, lst):
            for f in fields:
                v = float(getattr(row, f, 0) or 0)
                if v:
                    out.append((f"{lst}[{row.id}].{f}", v))
    return out


def _age(dob: str, anchor: date) -> int | None:
    try:
        d = date.fromisoformat(dob[:10])
    except ValueError:
        return None
    return anchor.year - d.year - ((anchor.month, anchor.day) < (d.month, d.day))


def prior_ages(doc: FnaDocument, anchor: date) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    people = [("client", doc.client)] + ([("spouse", doc.spouse)] if doc.household.includeSpouse or doc.spouse.dateOfBirth else [])
    for key, p in people:
        a = _age(p.dateOfBirth, anchor) if p.dateOfBirth else None
        if a is not None and 0 < a < 120:
            out.append((f"{key}.dateOfBirth", a))
        r = doc.retirement.get(key)
        if r and r.retirementAge:
            out.append((f"retirement.{key}.retirementAge", int(r.retirementAge)))
    for d in doc.dependants:
        a = _age(d.dateOfBirth, anchor) if d.dateOfBirth else None
        if a is not None and 0 <= a < 120:
            out.append((f"dependants[{d.id}].dateOfBirth", a))
    return out


def _clean_name(s: str) -> str | None:
    s = " ".join(s.split())
    norm = normalize_text(s)
    if not norm or norm in GENERIC_NAMES or len(norm) < 2:
        return None
    return s


def prior_names(doc: FnaDocument) -> list[tuple[str, str, PhraseKind]]:
    """(path, name, kind): people, employers, providers, insurers, lenders, fund and goal names."""
    out: list[tuple[str, str, PhraseKind]] = []
    for key, p in (("client", doc.client), ("spouse", doc.spouse)):
        full = f"{p.firstName} {p.surname}".strip()
        for path, name in ((f"{key}.firstName", p.firstName), (f"{key}.name", full), (f"{key}.employer", p.employer)):
            if name:
                out.append((path, name, "name"))
    for d in doc.dependants:
        if d.name:
            out.append((f"dependants[{d.id}].name", d.name, "name"))
            out.append((f"dependants[{d.id}].name", d.name.split()[0], "name"))
    for f in doc.retirementFunds:
        out += [(f"retirementFunds[{f.id}].provider", f.provider, "product"), (f"retirementFunds[{f.id}].name", f.name, "product")]
    for a in doc.assets:
        out += [(f"assets[{a.id}].provider", a.provider, "product"), (f"assets[{a.id}].description", a.description, "name")]
    for li in doc.liabilities:
        out.append((f"liabilities[{li.id}].lender", li.lender, "product"))
    for p in doc.policies:
        out.append((f"policies[{p.id}].insurer", p.insurer, "product"))
    for g in doc.goals:
        out.append((f"goals[{g.id}].name", g.name, "name"))
    return [(path, n, k) for path, name, k in out if (n := _clean_name(name or ""))]


def amount_phrases(value: float, *, source: str = "", distractors: bool = True) -> list[BiasPhrase]:
    phrases = [
        BiasPhrase(text=t, kind="amount", boost=BOOST["amount"], source=source) for t in amount_spoken_forms(value)[:FORMS_PER_AMOUNT]
    ]
    if distractors and value >= MIN_AMOUNT:
        for factor in (10.0, 0.1):
            for t in amount_spoken_forms(round(value * factor, 2))[:FORMS_PER_DISTRACTOR]:
                phrases.append(BiasPhrase(text=t, kind="distractor", boost=BOOST["distractor"], source=f"{source}×{factor:g}"))
    return phrases


def call_phrases(prior_fna: FnaDocument | None, *, anchor: date, crm: CrmNames | None = None, distractors: bool = True) -> list[BiasPhrase]:
    """The per-call phrase list (§5.3 per-call amount biasing, §5.4 client-specific names)."""
    phrases: list[BiasPhrase] = []
    if crm:
        phrases += [BiasPhrase(text=n, kind="name", boost=BOOST["name"], source="crm") for n in crm.all_names() if _clean_name(n)]
        for h in crm.products:  # the client's holdings and their institutions (§5.4)
            phrases += [BiasPhrase(text=n, kind="product", boost=BOOST["product"], source="crm") for n in (h.name, h.provider) if _clean_name(n)]
    if prior_fna is not None:
        for path, name, kind in prior_names(prior_fna):
            phrases.append(BiasPhrase(text=name, kind=kind, boost=BOOST[kind], source=path))
        seen: set[float] = set()
        for path, value in sorted(prior_amounts(prior_fna), key=lambda pv: -pv[1]):
            if value < MIN_AMOUNT or round(value, 2) in seen:
                continue
            seen.add(round(value, 2))
            phrases += amount_phrases(value, source=path, distractors=distractors)
        for path, age in prior_ages(prior_fna, anchor):
            phrases += [BiasPhrase(text=t, kind="age", boost=BOOST["age"], source=path) for t in age_spoken_forms(age)]
    return phrases


def build_vocabulary(
    prior_fna: FnaDocument | None = None,
    *,
    anchor: date,
    crm: CrmNames | None = None,
    include_static: bool = True,
    distractors: bool = True,
) -> Vocabulary:
    """Static vocabulary plus the per-call phrases, ready for an adapter."""
    vocab = Vocabulary(phrases=call_phrases(prior_fna, anchor=anchor, crm=crm, distractors=distractors))
    return vocab.merged(load_vocab_file()) if include_static else vocab


def product_names_for_call(prior_fna: FnaDocument | None, crm: CrmNames | None = None) -> tuple[ProductName, ...]:
    """The client's own products and providers, from the prior FnaDocument and the CRM's holdings, for the per-call
    product grammar (§5.3, §5.4)."""
    names: dict[str, ProductName] = {}

    def add(surface: str, ptype: str | None, provider_raw: str | None) -> None:
        s = _clean_name(surface or "")
        if not s or normalize_text(s) in _CATALOG_SURFACES:
            return
        provider = (provider_for(provider_raw) or _clean_name(provider_raw)) if provider_raw else None
        names.setdefault(normalize_text(s), ProductName(surface=s, product_type=ptype, provider=provider))

    for h in crm.products if crm else ():
        add(h.name, h.fna_type or None, h.provider or None)
        if h.provider and not provider_for(h.provider):
            add(h.provider, None, h.provider)
    if prior_fna is None:
        return tuple(sorted(names.values(), key=lambda n: n.surface))

    for f in prior_fna.retirementFunds:
        add(f.name, f.type, f.provider)
        if f.provider and not provider_for(f.provider):
            add(f.provider, None, f.provider)
    for a in prior_fna.assets:
        if a.provider and not provider_for(a.provider):
            add(a.provider, None, a.provider)
    for p in prior_fna.policies:
        if p.insurer and not provider_for(p.insurer):
            add(p.insurer, None, p.insurer)
    for li in prior_fna.liabilities:
        if li.lender and not provider_for(li.lender):
            add(li.lender, None, li.lender)
    return tuple(sorted(names.values(), key=lambda n: n.surface))
