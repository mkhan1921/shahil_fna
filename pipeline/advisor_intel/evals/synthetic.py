"""Synthetic calls (spec §15.1).

Each synthetic call starts from a ground-truth household sampled from the FNA app's sample household with every
field perturbed — archetypes cover in-community-of-property and accrual marriages, retirees, single people and
single parents, parents and siblings as dependants, the self-employed and young professionals, and every list
type (dependants, retirement funds, assets, liabilities, policies, goals, custom expenses, forecast-only events
and income changes). A planner decides what the call discusses and how, as a list of beats whose keyed phrases
carry the evidence; the script is then written either from templates (deterministic, offline) or by an LLM that
must keep every keyed phrase verbatim. The gold facts, spans and the gold FnaDocument come from the same plan, so
every gold fact is grounded in a script turn by construction.

The calls exercise what extraction gets wrong: vagueness ("about", "somewhere between"), hedges, revisions and
self-corrections, spouse interjections and unresolved contradictions, advisor-suggested values (confirmed or
not), implied values (ages, "in about eight years"), explicitly unknown values, slang and mixed-language amounts
("a bar", "two point five mil", "twee-honderd duisend") and off-topic chatter.

Synthetic data is never used to fit the calibrator or the conformal threshold (§15.1): every record carries the
``synthetic`` tag and ``assert_not_synthetic`` refuses such records wherever calibration data is assembled.
"""

from __future__ import annotations

import functools
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, NamedTuple, Protocol, Sequence

import numpy as np
from numpy.random import Generator
from pydantic import BaseModel

from .. import stats
from ..binding import load_prior, set_path
from ..config import PIPELINE_DIR
from ..models import fna as F
from ..models.dists import Triangular
from ..models.facts import Attribution, FactStatus
from ..models.runs import VersionPins
from ..models.transcript import Segment, Transcript, Word
from ..schema.slots import Catalog, fact_id_for, load_catalog
from ._runtime import call, get
from .gold import GoldCall, GoldEntity, GoldFact, GoldValue, Span, gold_value
from .spoken import nice_amount, possible_styles, say_age, say_amount, say_percent, say_period, words_en

SYNTHETIC_TAG = "synthetic"
SYNTHETIC_DATASET = "synthetic-calls"
SEED_HOUSEHOLD = PIPELINE_DIR / "tests" / "fixtures" / "sample_fna.json"
DEFAULT_ANCHOR = date(2026, 10, 15)
SpeakerRole = Literal["advisor", "client_primary", "client_secondary", "other"]

ARCHETYPES: tuple[str, ...] = (
    "family",
    "icop_couple",
    "accrual_couple",
    "retirees",
    "single",
    "single_parent",
    "sandwich",
    "self_employed",
    "young_professional",
)

# Lexicon seeds (§8.2): relative half-widths of approximation words, and band quantiles of age words.
APPROX_HALFWIDTH = {"about": 0.10, "roughly": 0.15, "in the ballpark of": 0.25, "give or take": 0.10}
TRI_Q = 1 - math.sqrt(0.2)  # the p10/p90 offset of a symmetric triangular, as a share of its half-width
AGE_BANDS = {"early sixties": (60.0, 61.5, 63.5), "mid sixties": (63.0, 65.0, 67.0), "late fifties": (57.0, 58.5, 59.9)}
LIKELIHOOD = {"might": (0.25, 0.40, 0.55), "probably": (0.60, 0.70, 0.80), "maybe": (0.20, 0.35, 0.50), "definitely": (0.90, 0.95, 0.99)}
SLANG_STYLES = frozenset({"short_k", "bar", "grand", "mil"})


# --------------------------------------------------------------------------- script structures


class ScriptTurn(NamedTuple):
    """One turn of a call script: who speaks and what they say. A tuple, so ``(speaker, text)`` unpacks too."""

    speaker: str
    text: str


class Piece(BaseModel):
    """Part of a turn; a keyed piece is evidence for the listed gold facts and must survive rewriting verbatim."""

    text: str
    keys: list[str] = []


class BeatTurn(BaseModel):
    speaker: str
    pieces: list[Piece]

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.pieces)


class Beat(BaseModel):
    """A short exchange about one topic."""

    topic: str
    turns: list[BeatTurn]


class SyntheticCall(BaseModel):
    call_id: str
    client_id: str
    index: int
    seed: int
    anchor_date: date
    archetype: str
    writer: Literal["template", "llm"] = "template"
    household: F.FnaDocument  # the full sampled truth
    prior_document: F.FnaDocument | None  # the client's FNA before the call (None: a first meeting)
    document: F.FnaDocument  # the gold document: the prior updated with what the call established
    script: list[ScriptTurn]
    gold: GoldCall
    phenomena: list[str] = []
    tags: list[str] = [SYNTHETIC_TAG]

    def transcript(self) -> Transcript:
        return script_transcript(self.script, self.anchor_date, self.call_id)

    @property
    def turns(self) -> list[ScriptTurn]:
        return self.script


# --------------------------------------------------------------------------- the synthetic-data guard


class SyntheticDataError(ValueError):
    """Raised when synthetic records reach calibration or conformal fitting (§15.1)."""


def is_synthetic(record: Any) -> bool:
    """True for anything tagged or sourced as synthetic: a GoldCall, a SyntheticCall, a dataset row or a label row."""
    if isinstance(record, SyntheticCall):
        return True
    tags = list(get(record, "tags", None) or [])
    meta = get(record, "metadata", None) or {}
    tags += list(get(meta, "tags", None) or [])
    if SYNTHETIC_TAG in tags:
        return True
    for holder in (record, meta):
        if get(holder, "source", None) == "synthetic" or get(holder, "dataset", None) == SYNTHETIC_DATASET:
            return True
    for holder in (record, meta, get(record, "input", None)):
        cid = get(holder, "call_id", None)
        if isinstance(cid, str) and cid.startswith("syn-"):
            return True
    return False


def assert_not_synthetic(records: Iterable[Any], *, purpose: str = "calibration") -> None:
    """Refuse synthetic records in data that fits the calibrator or the conformal threshold."""
    bad = [
        str(get(r, "call_id", None) or get(r, "id", None) or get(get(r, "input", None), "call_id", None) or "?")
        for r in records
        if is_synthetic(r)
    ]
    if bad:
        raise SyntheticDataError(f"synthetic data cannot be used for {purpose} (§15.1): {', '.join(sorted(set(bad))[:10])}")


def no_synthetic(purpose: str = "calibration") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator for fit functions: every list-like argument is checked with ``assert_not_synthetic``."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for a in [*args, *kwargs.values()]:
                if isinstance(a, (list, tuple)) and a and not isinstance(a[0], (int, float, str, bool)):
                    assert_not_synthetic(a, purpose=purpose)
            return fn(*args, **kwargs)

        return wrapper

    return deco


# --------------------------------------------------------------------------- the household


MALE = [
    "Thabo",
    "Sipho",
    "Johan",
    "Pieter",
    "Ahmed",
    "Rajesh",
    "Bongani",
    "Michael",
    "Riaan",
    "Themba",
    "Yusuf",
    "Pravin",
    "Lwazi",
    "Andile",
]
FEMALE = ["Lerato", "Naledi", "Anika", "Priya", "Fatima", "Zanele", "Chantel", "Ayesha", "Nomsa", "Karabo", "Lindiwe", "Megan", "Refilwe"]
KIDS_M = ["Neo", "Kagiso", "Luke", "Aiden", "Tumelo", "Ethan", "Sibusiso", "Zaid", "Liam", "Lethabo"]
KIDS_F = ["Thandi", "Amahle", "Zoe", "Mia", "Naledi", "Aaliyah", "Kea", "Emma", "Imani", "Lesedi"]
SURNAMES = ["Mokoena", "Naidoo", "van der Merwe", "Dlamini", "Botha", "Pillay", "Khumalo", "Nkosi", "Jacobs", "Mahlangu", "Patel", "Venter"]
JOBS: dict[str, list[tuple[str, str]]] = {
    "salaried": [
        ("financial manager", "Highveld Logistics"),
        ("HR business partner", "Brightwater Retail"),
        ("teacher", "the Department of Education"),
        ("nurse", "Sunrise Hospital"),
        ("software developer", "Karoo Digital"),
        ("engineer", "Vaal Mining Services"),
    ],
    "self-employed": [("plumber", "my own plumbing business"), ("architect", "my own practice"), ("caterer", "my own catering company")],
    "commission": [("sales representative", "Acacia Motors"), ("estate agent", "Protea Properties")],
    "contract": [("IT consultant", "a few clients on contract"), ("project manager", "a mining contract")],
    "retired": [("retired teacher", ""), ("retired accountant", "")],
    "unemployed": [("", "")],
    "student": [("student", "the university")],
}
INSURERS = ["Protea Life", "Baobab Assurance", "Karoo Mutual", "Highveld Insure", "Fynbos Life"]
PROVIDERS = ["Acacia Investments", "Kudu Asset Managers", "Marula Wealth", "Umbrella Pension Fund"]
GOALS = [
    "Family holiday overseas",
    "New car",
    "Kitchen renovation",
    "Deposit on a flat",
    "Wedding",
    "Gap year for the kids",
    "Solar installation",
]
CUSTOM_EXPENSES = [
    ("Church tithe", "family", False),
    ("Gogo's medication", "family", True),
    ("Pet insurance", "insurance", False),
    ("Stokvel contribution", "personal", False),
]


def _uid(ns: str, *parts: Any) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "advisor-intel/synthetic/" + ns + "/" + "/".join(str(p) for p in parts)))


def _choice(rng: Generator, seq: Sequence[Any]) -> Any:
    return seq[int(rng.integers(len(seq)))]


def _dob_for_age(rng: Generator, anchor: date, age: int) -> str:
    """A birthday within the 12 months before ``anchor − age`` years, so the person is ``age`` on the call date."""
    end = date(anchor.year - age, anchor.month, min(anchor.day, 28))
    return (end - timedelta(days=int(rng.integers(0, 365)))).isoformat()


def age_on(dob: str, anchor: date) -> int:
    d = date.fromisoformat(dob)
    return anchor.year - d.year - ((anchor.month, anchor.day) < (d.month, d.day))


def _luhn(digits: str) -> int:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            n -= 9 if n > 9 else 0
        total += n
    return (10 - total % 10) % 10


def sa_id_number(dob: str, male: bool, rng: Generator) -> str:
    """A valid South African ID number (Luhn check digit) for a date of birth and gender."""
    seq = int(rng.integers(5000, 10000)) if male else int(rng.integers(0, 5000))
    body = f"{dob[2:4]}{dob[5:7]}{dob[8:10]}{seq:04d}08"
    return body + str(_luhn(body))


def _money(rng: Generator, median: float, sigma: float, lo: float, hi: float) -> float:
    return nice_amount(float(np.clip(median * math.exp(sigma * rng.standard_normal()), lo, hi)))


def load_seed_household(path: Path | None = None) -> F.FnaDocument:
    """The FNA app's sample household (lib/fna/sample.ts, exported as JSON)."""
    return F.FnaDocument.model_validate(json.loads((path or SEED_HOUSEHOLD).read_text()))


@dataclass
class _Shape:
    couple: bool
    regimes: tuple[str, ...]
    kids: tuple[int, int]
    parents: float
    siblings: float
    ages: tuple[int, int]
    retired: bool = False
    employment: tuple[str, ...] = ("salaried", "salaried", "salaried", "commission", "contract", "self-employed")
    statuses: tuple[str, ...] = ("married",)


SHAPES: dict[str, _Shape] = {
    "family": _Shape(True, ("anc-accrual", "anc-no-accrual", "icop", "customary"), (1, 3), 0.2, 0.05, (30, 50)),
    "icop_couple": _Shape(True, ("icop",), (0, 2), 0.1, 0.0, (25, 60)),
    "accrual_couple": _Shape(True, ("anc-accrual",), (0, 3), 0.1, 0.0, (28, 60)),
    "retirees": _Shape(
        True, ("anc-accrual", "icop", "anc-no-accrual", "foreign"), (0, 0), 0.0, 0.0, (63, 78), retired=True, employment=("retired",)
    ),
    "single": _Shape(False, ("na",), (0, 0), 0.2, 0.1, (25, 65), statuses=("single", "divorced", "widowed")),
    "single_parent": _Shape(False, ("na",), (1, 3), 0.1, 0.0, (28, 50), statuses=("single", "divorced", "widowed")),
    "sandwich": _Shape(True, ("anc-accrual", "anc-no-accrual", "icop"), (1, 3), 1.0, 0.4, (35, 55)),
    "self_employed": _Shape(True, ("anc-no-accrual", "anc-accrual"), (0, 2), 0.1, 0.0, (30, 60), employment=("self-employed",)),
    "young_professional": _Shape(False, ("na",), (0, 0), 0.0, 0.1, (23, 32), employment=("salaried", "contract"), statuses=("single",)),
}


def sample_household(rng: Generator, anchor: date, archetype: str, *, ns: str, seed_doc: F.FnaDocument | None = None) -> F.FnaDocument:
    """A ground-truth FnaDocument: the sample household with every field perturbed for the archetype (§15.1)."""
    shape = SHAPES[archetype]
    base = (seed_doc or load_seed_household()).model_dump(mode="json")
    doc: dict[str, Any] = {
        "id": _uid(ns, "doc"),
        "schemaVersion": 2,
        "createdAt": f"{anchor.isoformat()}T08:00:00Z",
        "updatedAt": f"{anchor.isoformat()}T08:00:00Z",
        "status": "in-progress",
        "assumptions": base["assumptions"],
        "advice": F.AdviceRecord().model_dump(mode="json"),
        "notes": "",
    }
    surname = _choice(rng, SURNAMES)
    male = bool(rng.random() < 0.5)
    c_age = int(rng.integers(shape.ages[0], shape.ages[1] + 1))
    status = "married" if shape.couple and rng.random() < 0.85 else ("life-partner" if shape.couple else _choice(rng, shape.statuses))
    couple = shape.couple
    emp = _choice(rng, shape.employment) if not shape.retired else "retired"
    if not shape.retired and c_age >= 66 and rng.random() < 0.6:
        emp = "retired"

    def person(first: str, is_male: bool, age: int, employment: str) -> dict[str, Any]:
        dob = _dob_for_age(rng, anchor, age)
        occ, employer = _choice(rng, JOBS[employment])
        return F.Person(
            title="Mr" if is_male else "Ms",
            firstName=first,
            surname=surname,
            idNumber=sa_id_number(dob, is_male, rng),
            dateOfBirth=dob,
            gender="male" if is_male else "female",
            smoker=bool(rng.random() < 0.15),
            occupation=occ,
            employer=employer.removeprefix("my own ").title() if employer.startswith("my own") else employer,
            employmentType=employment,
            email=f"{first.lower()}@example.co.za",
            mobile=f"08{int(rng.integers(1, 5))} 555 {int(rng.integers(1000, 9999))}",
        ).model_dump(mode="json")

    doc["client"] = person(_choice(rng, MALE if male else FEMALE), male, c_age, emp)
    if couple:
        s_age = int(np.clip(c_age + rng.integers(-6, 5), 21, 85))
        s_emp = "retired" if shape.retired else _choice(rng, ("salaried", "salaried", "contract", "commission", "unemployed"))
        doc["spouse"] = person(_choice(rng, FEMALE if male else MALE), not male, s_age, s_emp)
    else:
        doc["spouse"] = F.Person().model_dump(mode="json")
    regime = _choice(rng, shape.regimes) if status == "married" else "na"
    married_years = int(rng.integers(2, max(3, min(c_age, 80) - 22)))
    doc["household"] = F.Household(
        maritalStatus=status,
        maritalRegime=regime,
        includeSpouse=couple,
        dateOfMarriage=(
            date(anchor.year - married_years, int(rng.integers(1, 13)), int(rng.integers(1, 28))).isoformat() if status == "married" else ""
        ),
        physicalAddress=base["household"]["physicalAddress"],
        city=base["household"]["city"],
        province=base["household"]["province"],
        postalCode=base["household"]["postalCode"],
    ).model_dump(mode="json")

    # dependants: children, parents and siblings
    deps: list[dict[str, Any]] = []
    n_kids = int(rng.integers(shape.kids[0], shape.kids[1] + 1))
    used: set[str] = set()
    for k in range(n_kids):
        boy = bool(rng.random() < 0.5)
        name = _choice(rng, [n for n in (KIDS_M if boy else KIDS_F) if n not in used] or (KIDS_M if boy else KIDS_F))
        used.add(name)
        age = int(rng.integers(1, max(2, min(24, c_age - 20))))
        school = "none" if age < 6 or age > 18 else ("private" if rng.random() < 0.35 else "public")
        tertiary = age < 23 and rng.random() < 0.75
        deps.append(
            F.Dependant(
                id=_uid(ns, "dependant", k),
                name=f"{name} {surname}",
                relationship="child",
                dateOfBirth=_dob_for_age(rng, anchor, age),
                gender="male" if boy else "female",
                dependencyEndAge=float(_choice(rng, (21, 22, 23, 25))),
                schoolType=school,
                schoolFeesAnnual=_money(rng, 150_000, 0.3, 60_000, 260_000)
                if school == "private"
                else (_money(rng, 25_000, 0.5, 4_000, 60_000) if school == "public" else 0),
                tertiary=tertiary,
                tertiaryStartAge=float(_choice(rng, (18, 18, 19))),
                tertiaryYears=float(_choice(rng, (3, 4, 4, 5))),
                tertiaryCostAnnual=_money(rng, 150_000, 0.35, 70_000, 600_000) if tertiary else 150_000,
                educationSavings=_money(rng, 80_000, 0.8, 5_000, 600_000) if tertiary and rng.random() < 0.5 else 0,
                educationSavingsMonthly=_money(rng, 1_500, 0.5, 300, 6_000) if tertiary and rng.random() < 0.4 else 0,
                specialNeeds=bool(rng.random() < 0.05),
            ).model_dump(mode="json")
        )
    if rng.random() < shape.parents:
        mum = bool(rng.random() < 0.7)
        deps.append(
            F.Dependant(
                id=_uid(ns, "dependant", "parent"),
                name=f"{_choice(rng, FEMALE if mum else MALE)} {surname}",
                relationship="parent",
                dateOfBirth=_dob_for_age(rng, anchor, int(np.clip(c_age + rng.integers(22, 34), 55, 95))),
                gender="female" if mum else "male",
                dependencyEndAge=90,
                monthlySupport=_money(rng, 3_000, 0.5, 800, 12_000),
                supportYears=float(_choice(rng, (5, 10, 15, 20))),
                schoolType="none",
                tertiary=False,
            ).model_dump(mode="json")
        )
    if rng.random() < shape.siblings:
        sis = bool(rng.random() < 0.5)
        deps.append(
            F.Dependant(
                id=_uid(ns, "dependant", "sibling"),
                name=f"{_choice(rng, FEMALE if sis else MALE)} {surname}",
                relationship="sibling",
                dateOfBirth=_dob_for_age(rng, anchor, int(rng.integers(16, 30))),
                gender="female" if sis else "male",
                dependencyEndAge=25,
                monthlySupport=_money(rng, 1_500, 0.5, 500, 5_000),
                supportYears=float(_choice(rng, (2, 3, 4))),
                schoolType="none",
                tertiary=False,
            ).model_dump(mode="json")
        )
    doc["dependants"] = deps

    # income
    def income(employment: str, age: int) -> dict[str, Any]:
        if employment in ("retired", "unemployed"):
            return F.Income(
                otherTaxableMonthly=_money(rng, 8_000, 0.6, 2_000, 40_000) if employment == "retired" and rng.random() < 0.3 else 0,
                uifApplies=False,
            ).model_dump(mode="json")
        if employment == "student":
            return F.Income(grossMonthly=_money(rng, 6_000, 0.4, 2_000, 15_000), uifApplies=False).model_dump(mode="json")
        gross = _money(rng, 25_000 + 1_100 * max(0, age - 22), 0.45, 9_000, 400_000)
        bonus = nice_amount(gross * float(_choice(rng, (0, 0, 1, 1, 2)))) if employment == "salaried" else 0.0
        return F.Income(
            grossMonthly=gross,
            annualBonus=bonus,
            otherTaxableMonthly=0,
            nonTaxableMonthly=0,
            medicalAidPayrollMonthly=nice_amount(2_000 * (1 + len(deps))) if employment == "salaried" and rng.random() < 0.6 else 0,
            medicalSchemeMembers=float(1 + len([d for d in deps if d["relationship"] == "child"]) + (1 if couple else 0))
            if employment == "salaried"
            else 0,
            uifApplies=employment in ("salaried", "commission"),
        ).model_dump(mode="json")

    doc["income"] = {
        "client": income(emp, c_age),
        "spouse": income(doc["spouse"]["employmentType"], 40) if couple else F.Income().model_dump(mode="json"),
    }
    total_income = doc["income"]["client"]["grossMonthly"] + doc["income"]["spouse"]["grossMonthly"] + 1.0

    # assets first (liabilities link to them)
    assets: list[dict[str, Any]] = []
    owner_joint = "joint" if couple else "client"
    has_home = rng.random() < (0.3 if archetype == "young_professional" else 0.8)
    if has_home:
        assets.append(
            F.Asset(
                id=_uid(ns, "asset", "home"),
                owner=owner_joint,
                type="primary-residence",
                description="Family home",
                value=_money(rng, 1_800_000 * total_income / 80_000, 0.35, 450_000, 15_000_000),
                baseCost=0,
                earmark="general",
            ).model_dump(mode="json")
        )
        assets[-1]["baseCost"] = nice_amount(assets[-1]["value"] * float(rng.uniform(0.4, 0.9)))
    for v in range(int(rng.integers(1, 3))):
        assets.append(
            F.Asset(
                id=_uid(ns, "asset", "vehicle", v),
                owner="client" if v == 0 or not couple else "spouse",
                type="vehicle",
                description=_choice(rng, ("Toyota Fortuner", "VW Polo", "Ford Ranger", "Suzuki Swift", "Hyundai i20")),
                value=_money(rng, 280_000, 0.4, 60_000, 1_200_000),
            ).model_dump(mode="json")
        )
    assets.append(
        F.Asset(
            id=_uid(ns, "asset", "cash"),
            owner=owner_joint,
            type="cash",
            description="Cheque and savings",
            provider="Bank",
            value=_money(rng, 30_000, 0.8, 2_000, 400_000),
        ).model_dump(mode="json")
    )
    if rng.random() < 0.6:
        assets.append(
            F.Asset(
                id=_uid(ns, "asset", "mm"),
                owner="client",
                type="money-market",
                description="Emergency fund",
                provider="Money market",
                value=_money(rng, 70_000, 0.7, 5_000, 600_000),
                monthlyContribution=_money(rng, 1_000, 0.6, 200, 8_000),
                earmark="emergency",
            ).model_dump(mode="json")
        )
    extra_types = ["unit-trust", "tfsa", "shares", "offshore", "endowment", "crypto", "residential-property"]
    for t in extra_types:
        if (
            rng.random()
            < {
                "unit-trust": 0.45,
                "tfsa": 0.45,
                "shares": 0.2,
                "offshore": 0.15,
                "endowment": 0.1,
                "crypto": 0.08,
                "residential-property": 0.12,
            }[t]
        ):
            val = _money(rng, 2_000_000 if t == "residential-property" else 180_000, 0.6, 5_000, 20_000_000)
            assets.append(
                F.Asset(
                    id=_uid(ns, "asset", t),
                    owner=_choice(rng, ("client", "spouse", "joint")) if couple else "client",
                    type=t,
                    description={
                        "unit-trust": "Balanced unit trust",
                        "tfsa": "Tax-free savings account",
                        "shares": "Share portfolio",
                        "offshore": "Offshore feeder fund",
                        "endowment": "Endowment policy",
                        "crypto": "Bitcoin",
                        "residential-property": "Rental flat",
                    }[t],
                    provider=_choice(rng, PROVIDERS),
                    value=val,
                    baseCost=nice_amount(val * float(rng.uniform(0.5, 0.95))),
                    monthlyContribution=_money(rng, 1_500, 0.6, 300, 10_000) if t in ("unit-trust", "tfsa") and rng.random() < 0.6 else 0,
                    monthlyIncome=_money(rng, 9_000, 0.4, 3_000, 40_000) if t == "residential-property" else 0,
                    earmark=_choice(rng, ("general", "education", "retirement", "goal")) if t in ("unit-trust", "tfsa") else "general",
                ).model_dump(mode="json")
            )
    if emp == "self-employed":
        assets.append(
            F.Asset(
                id=_uid(ns, "asset", "business"),
                owner="client",
                type="business-interest",
                description="Share of the business",
                value=_money(rng, 1_500_000, 0.7, 100_000, 30_000_000),
            ).model_dump(mode="json")
        )
    doc["assets"] = assets

    # liabilities
    liabilities: list[dict[str, Any]] = []
    home = next((a for a in assets if a["type"] == "primary-residence"), None)
    if home is not None and rng.random() < 0.8:
        bal = nice_amount(home["value"] * float(rng.uniform(0.25, 0.9)))
        rate = float(_choice(rng, (0.1075, 0.11, 0.1125, 0.1175)))
        liabilities.append(
            F.Liability(
                id=_uid(ns, "liability", "home"),
                owner=home["owner"],
                type="home-loan",
                lender="Bank",
                description="Home loan",
                balance=bal,
                interestRate=rate,
                monthlyRepayment=nice_amount(bal * (rate / 12) / (1 - (1 + rate / 12) ** -240)),
                linkedAssetId=home["id"],
            ).model_dump(mode="json")
        )
    for a in [a for a in assets if a["type"] == "vehicle"]:
        if rng.random() < 0.55:
            bal = nice_amount(a["value"] * float(rng.uniform(0.3, 0.9)))
            liabilities.append(
                F.Liability(
                    id=_uid(ns, "liability", "vehicle", a["id"]),
                    owner=a["owner"],
                    type="vehicle-finance",
                    lender="Bank",
                    description=f"{a['description']} finance",
                    balance=bal,
                    interestRate=0.1225,
                    monthlyRepayment=nice_amount(bal / 48),
                    linkedAssetId=a["id"],
                    creditLifeCover=bool(rng.random() < 0.5),
                ).model_dump(mode="json")
            )
    for t, p in (("credit-card", 0.65), ("personal-loan", 0.2), ("store-card", 0.2), ("overdraft", 0.1)):
        if rng.random() < p:
            bal = _money(rng, 25_000, 0.7, 1_000, 250_000)
            liabilities.append(
                F.Liability(
                    id=_uid(ns, "liability", t),
                    owner=_choice(rng, ("client", "spouse")) if couple else "client",
                    type=t,
                    lender="Bank",
                    description=t.replace("-", " ").capitalize(),
                    balance=bal,
                    interestRate=0.2075,
                    monthlyRepayment=nice_amount(max(300.0, bal * 0.05)),
                ).model_dump(mode="json")
            )
    if archetype == "young_professional" and rng.random() < 0.7:
        liabilities.append(
            F.Liability(
                id=_uid(ns, "liability", "student"),
                owner="client",
                type="student-loan",
                lender="Bank",
                description="Student loan",
                balance=_money(rng, 90_000, 0.5, 10_000, 400_000),
                interestRate=0.11,
                monthlyRepayment=_money(rng, 2_000, 0.4, 500, 8_000),
            ).model_dump(mode="json")
        )
    if emp == "self-employed" and rng.random() < 0.6:
        liabilities.append(
            F.Liability(
                id=_uid(ns, "liability", "business"),
                owner="client",
                type="business-loan",
                lender="Bank",
                description="Business loan",
                balance=_money(rng, 400_000, 0.6, 50_000, 5_000_000),
                interestRate=0.13,
                monthlyRepayment=_money(rng, 9_000, 0.5, 1_000, 80_000),
            ).model_dump(mode="json")
        )
    doc["liabilities"] = liabilities

    # retirement funds
    funds: list[dict[str, Any]] = []
    for who, person_emp, age in (("client", emp, c_age), ("spouse", doc["spouse"]["employmentType"], 40)):
        if who == "spouse" and not couple:
            continue
        scale = max(0.2, (age - 22) / 20) * (doc["income"][who]["grossMonthly"] or 20_000) / 50_000
        if person_emp == "retired":
            funds.append(
                F.RetirementFund(
                    id=_uid(ns, "fund", who, "la"),
                    owner=who,
                    type="living-annuity",
                    provider=_choice(rng, PROVIDERS),
                    name="Living annuity",
                    value=_money(rng, 3_000_000, 0.6, 300_000, 30_000_000),
                    viaPayroll=False,
                    drawdownRate=float(_choice(rng, (0.04, 0.05, 0.06, 0.075))),
                ).model_dump(mode="json")
            )
            if rng.random() < 0.3:
                funds.append(
                    F.RetirementFund(
                        id=_uid(ns, "fund", who, "life"),
                        owner=who,
                        type="life-annuity",
                        provider=_choice(rng, INSURERS),
                        name="Life annuity",
                        value=_money(rng, 900_000, 0.5, 100_000, 6_000_000),
                        viaPayroll=False,
                    ).model_dump(mode="json")
                )
            continue
        if person_emp in ("salaried", "commission"):
            t = _choice(rng, ("pension", "provident"))
            gross = doc["income"][who]["grossMonthly"]
            funds.append(
                F.RetirementFund(
                    id=_uid(ns, "fund", who, "work"),
                    owner=who,
                    type=t,
                    provider="Employer umbrella fund",
                    name=f"{t.title()} fund",
                    value=_money(rng, 900_000 * scale, 0.5, 20_000, 40_000_000),
                    savingsPot=_money(rng, 25_000, 0.5, 2_000, 200_000),
                    employeeMonthly=nice_amount(gross * 0.075),
                    employerMonthly=nice_amount(gross * 0.1),
                    viaPayroll=True,
                    beneficiariesNominated=bool(rng.random() < 0.7),
                ).model_dump(mode="json")
            )
        if rng.random() < (0.9 if person_emp in ("self-employed", "contract") else 0.4):
            funds.append(
                F.RetirementFund(
                    id=_uid(ns, "fund", who, "ra"),
                    owner=who,
                    type="retirement-annuity",
                    provider=_choice(rng, PROVIDERS),
                    name="Retirement annuity",
                    value=_money(rng, 300_000 * scale, 0.7, 5_000, 20_000_000),
                    employeeMonthly=_money(rng, 2_500, 0.6, 500, 30_000),
                    viaPayroll=False,
                ).model_dump(mode="json")
            )
        if rng.random() < 0.25:
            pt = _choice(rng, ("preservation-pension", "preservation-provident"))
            funds.append(
                F.RetirementFund(
                    id=_uid(ns, "fund", who, "pres"),
                    owner=who,
                    type=pt,
                    provider=_choice(rng, PROVIDERS),
                    name="Preservation fund",
                    value=_money(rng, 250_000, 0.6, 10_000, 5_000_000),
                    viaPayroll=False,
                ).model_dump(mode="json")
            )
    doc["retirementFunds"] = funds

    # policies
    policies: list[dict[str, Any]] = []
    for who in ("client", "spouse") if couple else ("client",):
        p_emp = doc[who]["employmentType"]
        gross = doc["income"][who]["grossMonthly"]
        working = p_emp not in ("retired", "unemployed", "student")
        if rng.random() < (0.85 if working else 0.4):
            policies.append(
                F.Policy(
                    id=_uid(ns, "policy", who, "life"),
                    lifeAssured=who,
                    type="life",
                    insurer=_choice(rng, INSURERS),
                    policyNumber=f"L-{int(rng.integers(10**7, 10**8))}",
                    cover=_money(rng, max(500_000, gross * 40), 0.4, 100_000, 50_000_000),
                    beneficiary="spouse" if couple else _choice(rng, ("estate", "children", "other")),
                    premiumMonthly=_money(rng, 800, 0.5, 100, 8_000),
                    inceptionDate=date(anchor.year - int(rng.integers(1, 15)), 3, 1).isoformat(),
                ).model_dump(mode="json")
            )
        if working and p_emp == "salaried" and rng.random() < 0.7:
            policies.append(
                F.Policy(
                    id=_uid(ns, "policy", who, "group"),
                    lifeAssured=who,
                    type="life",
                    insurer="Employer group scheme",
                    isGroup=True,
                    cover=nice_amount(gross * 12 * 3),
                    beneficiary="spouse" if couple else "estate",
                ).model_dump(mode="json")
            )
        if working and rng.random() < 0.55:
            policies.append(
                F.Policy(
                    id=_uid(ns, "policy", who, "ip"),
                    lifeAssured=who,
                    type="income-protection",
                    insurer=_choice(rng, INSURERS),
                    isGroup=p_emp == "salaried" and rng.random() < 0.6,
                    monthlyBenefit=nice_amount(gross * float(rng.uniform(0.5, 0.75))),
                    waitingPeriodMonths=float(_choice(rng, (1, 3, 3, 6))),
                    benefitToAge=65,
                ).model_dump(mode="json")
            )
        for t, p in (("disability-lump", 0.35), ("severe-illness", 0.4), ("funeral", 0.45), ("family-income", 0.08)):
            if rng.random() < (p if working else p / 3):
                policies.append(
                    F.Policy(
                        id=_uid(ns, "policy", who, t),
                        lifeAssured=who,
                        type=t,
                        insurer=_choice(rng, INSURERS),
                        cover=_money(rng, 30_000 if t == "funeral" else 1_000_000, 0.5, 10_000, 20_000_000) if t != "family-income" else 0,
                        monthlyBenefit=_money(rng, 20_000, 0.4, 5_000, 100_000) if t == "family-income" else 0,
                        accelerated=t == "severe-illness" and rng.random() < 0.5,
                        premiumMonthly=_money(rng, 300, 0.6, 50, 4_000),
                    ).model_dump(mode="json")
                )
    doc["policies"] = policies

    # retirement goals
    def goal(who: str) -> dict[str, Any]:
        age = age_on(doc[who]["dateOfBirth"], anchor) if doc[who]["dateOfBirth"] else 40
        ret_age = (
            min(age, int(_choice(rng, (60, 63, 65, 65))))
            if doc[who]["employmentType"] == "retired"
            else int(_choice(rng, (55, 58, 60, 62, 63, 65, 65, 67)))
        )
        ret_age = max(ret_age, min(age + 1, 70)) if doc[who]["employmentType"] != "retired" else ret_age
        amount_mode = rng.random() < 0.3
        return F.RetirementGoal(
            retirementAge=float(ret_age),
            planningAge=float(_choice(rng, (88, 90, 92, 95, 100))),
            targetMode="amount" if amount_mode else "percent",
            targetPercent=float(_choice(rng, (0.6, 0.7, 0.75, 0.8))),
            targetMonthly=_money(rng, 45_000, 0.5, 8_000, 300_000) if amount_mode else 0,
            otherIncomeMonthly=_money(rng, 6_000, 0.5, 1_000, 40_000) if rng.random() < 0.15 else 0,
        ).model_dump(mode="json")

    doc["retirement"] = {"client": goal("client"), "spouse": goal("spouse") if couple else F.RetirementGoal().model_dump(mode="json")}

    goals = []
    for g in range(int(rng.integers(0, 4))):
        name = GOALS[(int(rng.integers(len(GOALS))) + g) % len(GOALS)]
        if any(x["name"] == name for x in goals):
            continue
        goals.append(
            F.OtherGoal(
                id=_uid(ns, "goal", g),
                owner=_choice(rng, ("client", "spouse", "joint")) if couple else "client",
                name=name,
                cost=_money(rng, 150_000, 0.7, 20_000, 2_000_000),
                targetYear=anchor.year + int(rng.integers(1, 9)),
                existingSavings=_money(rng, 15_000, 0.8, 1_000, 500_000) if rng.random() < 0.6 else 0,
                monthlyContribution=_money(rng, 1_500, 0.6, 200, 20_000) if rng.random() < 0.6 else 0,
                priority=_choice(rng, ("high", "medium", "low")),
            ).model_dump(mode="json")
        )
    doc["goals"] = goals

    # expenses: the sample budget scaled to the household, with lines added and dropped
    scale = float(np.clip((total_income / 155_000) ** 0.6, 0.35, 3.0))  # budgets grow slower than income
    items: dict[str, float] = {}
    has_school = any(d["schoolType"] != "none" for d in deps)
    for key, val in base["expenses"]["items"].items():
        if key == "school-fees" and not has_school or key in ("childcare", "extramurals", "school-other") and not n_kids:
            continue
        if key == "family-support":
            sup = sum(d["monthlySupport"] for d in deps)
            if sup:
                items[key] = nice_amount(sup)
            continue
        if rng.random() < 0.85:
            items[key] = nice_amount(max(100.0, val * scale * math.exp(0.25 * rng.standard_normal())))
    if not has_home:
        items["rent"] = _money(rng, 9_000 * scale, 0.4, 2_500, 60_000)
    customs = []
    for c in range(int(rng.integers(0, 3))):
        label, cat, essential = CUSTOM_EXPENSES[(int(rng.integers(len(CUSTOM_EXPENSES))) + c) % len(CUSTOM_EXPENSES)]
        if any(x["label"] == label for x in customs):
            continue
        customs.append(
            F.CustomExpense(
                id=_uid(ns, "custom", c), category=cat, label=label, amount=_money(rng, 600, 0.6, 100, 5_000), essential=essential
            ).model_dump(mode="json")
        )
    doc["expenses"] = {"items": items, "custom": customs}

    # estate
    def estate(who: str) -> dict[str, Any]:
        has_will = _choice(rng, ("yes", "yes", "no", "unsure"))
        minors = any(age_on(d["dateOfBirth"], anchor) < 18 for d in deps if d["relationship"] == "child")
        return F.EstateProfile(
            hasWill=has_will,
            willDate=date(anchor.year - int(rng.integers(1, 15)), int(rng.integers(1, 13)), 10).isoformat() if has_will == "yes" else "",
            executor="Bank trust company" if has_will == "yes" else "",
            guardianNominated=_choice(rng, ("yes", "no")) if minors else "na",
            hasTrust=bool(rng.random() < 0.08),
            funeralCost=float(_choice(rng, (30_000, 40_000, 50_000, 60_000))),
            accrualCommencementValue=_money(rng, 150_000, 0.6, 10_000, 3_000_000) if regime == "anc-accrual" else 0,
            additionalCapitalNeed=_money(rng, 200_000, 0.5, 20_000, 3_000_000) if rng.random() < 0.1 else 0,
        ).model_dump(mode="json")

    doc["estate"] = {"client": estate("client"), "spouse": estate("spouse") if couple else F.EstateProfile().model_dump(mode="json")}
    doc["riskProfile"] = {
        "answers": {
            q: float(rng.integers(0, 5))
            for q in ("horizon", "income-stability", "emergency", "proportion", "knowledge", "objective", "drawdown", "tradeoff")
        },
        "notes": "",
    }
    eng = dict(base["engagement"])
    eng.update(
        meetingDate=anchor.isoformat(),
        meetingType=_choice(rng, ("in-person", "virtual", "telephonic")),
        reasonForAdvice=_choice(
            rng,
            (
                "Annual review of the family's cover and savings.",
                "First meeting after a new job.",
                "Planning for retirement in the next few years.",
                "We want to check the kids' education savings.",
            ),
        ),
        clientObjectives="",
        clientConcerns=_choice(rng, (*CONCERNS, "")),
    )
    doc["engagement"] = eng
    return F.FnaDocument.model_validate(doc)


# --------------------------------------------------------------------------- the plan: beats and gold facts


def K(text: str, *keys: str) -> Piece:
    return Piece(text=text, keys=list(keys))


def T(speaker: str, *items: str | Piece) -> BeatTurn:
    """A turn from text and keyed pieces; a sentence stop after a phrase that already ends in one ('p.m.') is dropped."""
    pieces: list[Piece] = []
    for i in items:
        p = i if isinstance(i, Piece) else Piece(text=i)
        if not p.keys and pieces and pieces[-1].text.endswith(".") and p.text.startswith("."):
            p = Piece(text=p.text[1:])
        if p.text or p.keys:
            pieces.append(p)
    return BeatTurn(speaker=speaker, pieces=pieces)


def _decap(s: str) -> str:
    """Lower-case a capitalised first word ('More than 10 years' → 'more than 10 years'); 'R70,000' stays."""
    return s[0].lower() + s[1:] if len(s) > 1 and s[0].isupper() and s[1].islower() else s


OFF_TOPIC = [
    "Sorry, give me a second, the dog is barking at the gate.",
    "Did you watch the Springboks on Saturday? What a game.",
    "The traffic on the N1 this morning was unbelievable.",
    "Ag, the load-shedding schedule changed again last night.",
    "We had a braai on Sunday, the weather was perfect.",
    "Sorry, my phone was on mute. Can you hear me now?",
    "The kids are on school holidays, so the house is chaos.",
]
EMPLOYMENT_PHRASES = {
    "salaried": "employed full-time",
    "self-employed": "self-employed, I run my own business",
    "commission": "on commission, mostly",
    "contract": "a contractor",
    "retired": "retired",
    "unemployed": "between jobs at the moment",
    "student": "still studying",
}
REGIME_PHRASES = {
    "icop": "married in community of property",
    "anc-accrual": "an antenuptial contract with the accrual system",
    "anc-no-accrual": "an antenuptial contract without accrual",
    "customary": "married under customary law",
    "foreign": "married overseas, in the UK",
}
MARITAL_PHRASES = {
    "married": "married",
    "life-partner": "not married, we live together as partners",
    "single": "single",
    "divorced": "divorced",
    "widowed": "widowed",
}
FUND_PHRASES = {
    "pension": "a pension fund at work",
    "provident": "a provident fund through work",
    "retirement-annuity": "a retirement annuity",
    "preservation-pension": "a pension preservation fund",
    "preservation-provident": "a provident preservation fund",
    "living-annuity": "a living annuity",
    "life-annuity": "a guaranteed life annuity",
}
ASSET_PHRASES = {  # nouns that follow an owner word: "our house", "my unit trusts", "Lerato's tax-free savings account"
    "primary-residence": "house",
    "residential-property": "rental flat",
    "vehicle": "car",
    "cash": "cash in the bank",
    "money-market": "money market account",
    "unit-trust": "unit trusts",
    "shares": "share portfolio",
    "tfsa": "tax-free savings account",
    "endowment": "endowment policy",
    "offshore": "offshore investment",
    "business-interest": "share of the business",
    "crypto": "bitcoin",
}
CONCERNS = (
    "Worried the life cover is too low.",
    "Not sure we are saving enough for retirement.",
    "Concerned about our debt.",
)
EARMARK_PHRASES = {
    "emergency": "that's our rainy-day money",
    "education": "that's for the kids' varsity",
    "retirement": "that's for retirement",
    "goal": "we're saving it for something specific",
}
LIABILITY_PHRASES = {
    "home-loan": "the bond",
    "vehicle-finance": "car finance",
    "credit-card": "the credit card",
    "store-card": "store cards",
    "personal-loan": "personal loan",
    "student-loan": "student loan",
    "business-loan": "business loan",
    "overdraft": "overdraft",
}
POLICY_PHRASES = {
    "life": "life cover",
    "disability-lump": "lump-sum disability cover",
    "income-protection": "income protection",
    "severe-illness": "dread disease cover",
    "funeral": "a funeral policy",
    "family-income": "a family income benefit",
}
WILL_PHRASES = {"yes": "I've got a will", "no": "I don't have a will", "unsure": "I'm not sure if I have a valid will"}
GUARDIAN_PHRASES = {"yes": "we've named a guardian for the kids", "no": "we haven't named a guardian for the kids"}
TIER_P = {"A": 0.85, "B": 0.5, "C": 0.3}


class _Planner:
    """Decides what the call says about each fact, writes the beats, and records the matching gold facts."""

    def __init__(self, doc: F.FnaDocument, rng: Generator, anchor: date, catalog: Catalog, *, phenomenon_rate: float = 1.0) -> None:
        self.doc = doc
        self.data = doc.model_dump(mode="json")
        self.rng = rng
        self.anchor = anchor
        self.catalog = catalog
        self.rate = phenomenon_rate
        self.beats: list[Beat] = []
        self.facts: dict[str, GoldFact] = {}
        self.entities: dict[str, GoldEntity] = {}
        self.writes: dict[str, Any] = {}  # concrete path → value the call established (for the gold document)
        self.discussed_rows: set[tuple[str, str]] = set()
        self.phenomena: list[str] = []
        self.couple = bool(self.data["household"]["includeSpouse"])
        self.spouse_present = self.couple and rng.random() < 0.7
        self.client_name = self.data["client"]["firstName"]
        self.spouse_name = self.data["spouse"]["firstName"]
        self.spouse_she = self.data["spouse"]["gender"] == "female"

    # ---------------------------------------------------------------- helpers

    def p(self, x: float) -> bool:
        return bool(self.rng.random() < x)

    def discuss(self, slot_id: str) -> bool:
        tier = self.catalog.slot(slot_id).sensitivity_tier
        return self.p(TIER_P[tier])

    def speaker(self, who: str) -> str:
        return "client_secondary" if who == "spouse" and self.spouse_present else "client_primary"

    def you(self, who: str, question: str) -> str:
        """A question to the person themselves; the spouse on the call is addressed by name."""
        return f"And {self.spouse_name}, {question[0].lower()}{question[1:]}" if who == "spouse" else question

    def first_person(self, who: str) -> bool:
        return self.speaker(who) == ("client_secondary" if who == "spouse" else "client_primary")

    def name(self, who: str) -> str:
        return self.client_name if who == "client" else self.spouse_name

    def fact(
        self,
        slot_id: str,
        inst: str | None,
        path: str,
        status: FactStatus,
        attribution: Attribution = "client",
        value: GoldValue | None = None,
        raw: Any = None,
    ) -> str:
        fid = fact_id_for(slot_id, inst)
        self.facts[fid] = GoldFact(slot_id=slot_id, instance_key=inst, fna_path=path, status=status, attribution=attribution, value=value)
        if raw is not None and status in ("stated", "implied", "contradicted") and path and self.catalog.slot(slot_id).exported:
            self.writes[path] = raw
        return fid

    def unique(self, eid: str) -> str:
        if eid not in self.entities:
            return eid
        n = 2
        while f"{eid}_{n}" in self.entities:
            n += 1
        return f"{eid}_{n}"

    def entity(self, eid: str, etype: str, fna_list: str | None, row_id: str | None, names: list[str], relation: str = "") -> str:
        eid = self.unique(eid)
        self.entities[eid] = GoldEntity(
            entity_id=eid, type=etype, fna_list=fna_list, fna_id=row_id, names=[n for n in names if n], relation=relation
        )
        if fna_list and row_id:
            self.discussed_rows.add((fna_list, row_id))
        return eid

    def style(self, v: float) -> str:
        styles = possible_styles(v)
        r = self.rng.random()
        if r < 0.22 * self.rate and any(s in SLANG_STYLES for s in styles):
            return _choice(self.rng, [s for s in styles if s in SLANG_STYLES])
        if r < 0.28 * self.rate and "afrikaans" in styles:
            return "afrikaans"
        if r < 0.45 and "words" in styles and v < 10**7:
            return "words"
        return "digits"

    def money(self, v: float, period: str = "once") -> tuple[str, GoldValue, str]:
        """(spoken phrase, gold value, phenomenon) for an amount the client states."""
        per = say_period(period, int(self.rng.integers(4)))
        style = self.style(v)
        r = self.rng.random()
        if r < 0.16 * self.rate:
            word = _choice(self.rng, list(APPROX_HALFWIDTH))
            h = APPROX_HALFWIDTH[word] * TRI_Q
            phrase = f"{say_amount(v, style)}{per}, give or take" if word == "give or take" else f"{word} {say_amount(v, style)}{per}"
            return phrase, GoldValue(kind="numeric", p10=v * (1 - h), p50=v, p90=v * (1 + h), vague=True), "approx"
        if r < 0.26 * self.rate:
            lo, hi = nice_amount(v * 0.9), nice_amount(v * 1.1)
            if lo < v < hi:
                return (
                    f"somewhere between {say_amount(lo, 'digits')} and {say_amount(hi, 'digits')}{per}",
                    GoldValue(kind="numeric", p10=lo, p50=v, p90=hi, vague=True),
                    "range",
                )
        phen = "slang" if style in SLANG_STYLES else ("mixed_language" if style == "afrikaans" else "exact")
        return f"{say_amount(v, style)}{per}", GoldValue(kind="numeric", p50=v), phen

    def wrong(self, v: float) -> float:
        for _ in range(5):
            w = nice_amount(v * float(_choice(self.rng, (0.7, 0.8, 1.2, 1.3))))
            if w != v and w > 0:
                return w
        return v + 1000

    def amount_exchange(
        self,
        who: str,
        question: str,
        sentence: str,
        slot_id: str,
        inst: str | None,
        path: str,
        v: float,
        period: str,
        *,
        unknown: str | None = None,
        allow_spouse: bool = True,
    ) -> list[BeatTurn]:
        """An advisor question and the client's answer about one amount, with a phenomenon chosen at random."""
        spk = self.speaker(who)
        fid = fact_id_for(slot_id, inst)
        turns = [T("advisor", question)]
        r = self.rng.random()
        other = "client_primary" if spk == "client_secondary" else ("client_secondary" if self.spouse_present else None)
        if unknown and r < 0.06 * self.rate:
            turns.append(T(spk, K("I honestly don't know", fid), f" {unknown}."))
            self.fact(slot_id, inst, path, "explicitly_unknown")
            self.phenomena.append("explicitly_unknown")
            return turns
        if r < 0.14 * self.rate:
            w = self.wrong(v)
            phrase, gv, _ = self.money(v, period)
            a, _, b = sentence.partition("{v}")
            turns.append(
                T(
                    spk,
                    a,
                    K(say_amount(w, "digits") + say_period(period), fid),
                    b.rstrip(".") + "... ",
                    "no wait, sorry, it's ",
                    K(phrase, fid),
                    ".",
                )
            )
            self.fact(slot_id, inst, path, "stated", "client", gv, v)
            self.phenomena.append("self_correction")
            return turns
        if other and allow_spouse and r < 0.22 * self.rate:
            w = self.wrong(v)
            phrase, gv, _ = self.money(v, period)
            a, _, b = sentence.partition("{v}")
            turns.append(T(spk, "I think ", _lower_first(a), K(say_amount(w, "digits") + say_period(period), fid), b))
            if self.p(0.65):
                turns.append(T(other, "No, it's more like ", K(phrase, fid), " now."))
                turns.append(T(spk, "Ja, that's right."))
                self.fact(slot_id, inst, path, "stated", "client", gv, v)
                self.phenomena.append("spouse_correction")
            else:
                turns.append(T(other, "No, I'm sure it's ", K(say_amount(v, "digits") + say_period(period), fid), "."))
                turns.append(T(spk, "Well, we'll have to check the statements."))
                lo, hi = min(v, w), max(v, w)
                self.fact(slot_id, inst, path, "contradicted", "mixed", GoldValue(kind="numeric", p10=lo, p50=v, p90=hi, vague=True), v)
                self.phenomena.append("spouse_contradiction")
            return turns
        phrase, gv, phen = self.money(v, period)
        hedge = _choice(self.rng, ("", "", "", "I think ", "Probably ", "If I remember correctly, "))
        a, _, b = sentence.partition("{v}")
        turns.append(T(spk, hedge + (_lower_first(a) if hedge else a), K(phrase, fid), b))
        self.fact(slot_id, inst, path, "stated", "client", gv, v)
        self.phenomena.append(phen if not hedge else "hedge")
        return turns

    def beat(self, topic: str, turns: list[BeatTurn]) -> None:
        if turns:
            self.beats.append(Beat(topic=topic, turns=turns))

    # ---------------------------------------------------------------- topics

    def opening(self) -> None:
        names = self.client_name + (f" and {self.spouse_name}" if self.spouse_present else "")
        self.beat(
            "opening",
            [
                T("advisor", f"Good morning {names}, thanks for making the time. Today I'd like to update your financial needs analysis."),
                T("client_primary", "Morning! Ja, it's been a while, a lot has changed."),
            ],
        )

    def people(self) -> None:
        for who in ("client", "spouse") if self.couple else ("client",):
            d = self.data[who]
            spk, me = self.speaker(who), self.first_person(who)
            turns: list[BeatTurn] = []
            if d["dateOfBirth"] and self.discuss("{p}.dateOfBirth"):
                age = age_on(d["dateOfBirth"], self.anchor)
                truth = stats.iso_to_year(d["dateOfBirth"])
                fid = fact_id_for("{p}.dateOfBirth", who)
                turns.append(T("advisor", self.you(who, "How old are you now?") if me else f"And how old is {self.name(who)}?"))
                if self.p(0.75):
                    turns.append(
                        T(spk, K(f"I'm {say_age(age)}" if me else f"{'She' if self.spouse_she else 'He'}'s {say_age(age)}", fid), ".")
                    )
                    self.fact(
                        "{p}.dateOfBirth",
                        who,
                        f"{who}.dateOfBirth",
                        "implied",
                        "client",
                        GoldValue(kind="numeric", p10=truth - 0.5, p50=truth, p90=truth + 0.5),
                        d["dateOfBirth"],
                    )
                    self.phenomena.append("implied")
                else:
                    born = date.fromisoformat(d["dateOfBirth"])
                    lead = "I was born on " if me else f"{'She' if self.spouse_she else 'He'} was born on "
                    turns.append(T(spk, lead, K(f"{born.day} {born.strftime('%B')} {born.year}", fid), "."))
                    self.fact(
                        "{p}.dateOfBirth",
                        who,
                        f"{who}.dateOfBirth",
                        "stated",
                        "client",
                        GoldValue(kind="numeric", p50=truth),
                        d["dateOfBirth"],
                    )
            if self.discuss("{p}.employmentType"):
                emp = d["employmentType"]
                fid = fact_id_for("{p}.employmentType", who)
                turns.append(T("advisor", self.you(who, "What do you do for a living?") if me else f"And what does {self.name(who)} do?"))
                items: list[str | Piece] = ["I'm " if me else f"{self.name(who)} is ", K(EMPLOYMENT_PHRASES[emp], fid)]
                self.fact(
                    "{p}.employmentType", who, f"{who}.employmentType", "stated", "client", GoldValue(kind="categorical", label=emp), emp
                )
                if d["occupation"] and emp not in ("unemployed",) and self.discuss("{p}.occupation"):
                    occ_fid = fact_id_for("{p}.occupation", who)
                    items += [", working as a ", K(d["occupation"], occ_fid)]
                    self.fact(
                        "{p}.occupation",
                        who,
                        f"{who}.occupation",
                        "stated",
                        "client",
                        GoldValue(kind="text", label=d["occupation"]),
                        d["occupation"],
                    )
                turns.append(T(spk, *items, "."))
            if self.discuss("{p}.smoker"):
                fid = fact_id_for("{p}.smoker", who)
                turns.append(T("advisor", self.you(who, "Do you smoke at all?") if me else f"Does {self.name(who)} smoke?"))
                text = ("Ja, I do smoke" if me else "Ja, they smoke") if d["smoker"] else ("No, I don't smoke" if me else "No, never")
                turns.append(T(spk, K(text, fid), "."))
                self.fact(
                    "{p}.smoker",
                    who,
                    f"{who}.smoker",
                    "stated",
                    "client",
                    GoldValue(kind="boolean", label="true" if d["smoker"] else "false"),
                    d["smoker"],
                )
            self.beat("people", turns)

    def household(self) -> None:
        h = self.data["household"]
        turns: list[BeatTurn] = []
        if self.discuss("household.maritalStatus"):
            fid = fact_id_for("household.maritalStatus", None)
            turns.append(T("advisor", "And your relationship status, for the record?"))
            turns.append(T("client_primary", "We're " if self.couple else "I'm ", K(MARITAL_PHRASES[h["maritalStatus"]], fid), "."))
            self.fact(
                "household.maritalStatus",
                None,
                "household.maritalStatus",
                "stated",
                "client",
                GoldValue(kind="categorical", label=h["maritalStatus"]),
                h["maritalStatus"],
            )
        if h["maritalStatus"] == "married" and h["maritalRegime"] in REGIME_PHRASES and self.discuss("household.maritalRegime"):
            fid = fact_id_for("household.maritalRegime", None)
            turns.append(T("advisor", "Are you married in community of property, or did you sign an antenuptial contract?"))
            spk = "client_secondary" if self.spouse_present and self.p(0.4) else "client_primary"
            hedge = "I think " if self.p(0.2) else ""
            turns.append(T(spk, "I think we're " if hedge else "We're ", K(REGIME_PHRASES[h["maritalRegime"]], fid), "."))
            self.fact(
                "household.maritalRegime",
                None,
                "household.maritalRegime",
                "stated",
                "client",
                GoldValue(kind="categorical", label=h["maritalRegime"]),
                h["maritalRegime"],
            )
        self.beat("household", turns)

    def dependants(self) -> None:
        for n, d in enumerate(self.data["dependants"]):
            if not self.p(0.9):
                continue
            first = d["name"].split()[0]
            rel = d["relationship"]
            word = {
                "child": "son" if d["gender"] == "male" else "daughter",
                "parent": "mother" if d["gender"] == "female" else "father",
                "sibling": "brother" if d["gender"] == "male" else "sister",
                "other": "relative",
            }[rel]
            eid = self.entity(f"person.{_slug(first)}", "person", "dependants", d["id"], [first, d["name"], word], rel)
            path = f"dependants[{d['id']}]"
            turns: list[BeatTurn] = [
                T("advisor", "Who else depends on you financially?" if n else "Tell me about the people who depend on you.")
            ]
            rel_fid = fact_id_for("dependants[].relationship", eid)
            name_fid = fact_id_for("dependants[].name", eid)
            dob_fid = fact_id_for("dependants[].dateOfBirth", eid)
            age = age_on(d["dateOfBirth"], self.anchor)
            truth = stats.iso_to_year(d["dateOfBirth"])
            pron = "he" if d["gender"] == "male" else "she"
            turns.append(
                T(
                    "client_primary",
                    "There's ",
                    K(f"our {word}" if rel == "child" and self.couple else f"my {word}", rel_fid),
                    " ",
                    K(first, name_fid),
                    ", ",
                    K(f"{pron}'s {say_age(age)}", dob_fid),
                    ".",
                )
            )
            self.fact(
                "dependants[].relationship", eid, f"{path}.relationship", "stated", "client", GoldValue(kind="categorical", label=rel), rel
            )
            self.fact("dependants[].name", eid, f"{path}.name", "stated", "client", GoldValue(kind="text", label=first), d["name"])
            self.fact(
                "dependants[].dateOfBirth",
                eid,
                f"{path}.dateOfBirth",
                "implied",
                "client",
                GoldValue(kind="numeric", p10=truth - 0.5, p50=truth, p90=truth + 0.5),
                d["dateOfBirth"],
            )
            if d["specialNeeds"]:
                fid = fact_id_for("dependants[].specialNeeds", eid)
                turns.append(
                    T("client_primary", K(f"{pron.capitalize()} has special needs", fid), ", so that's a big part of our planning.")
                )
                self.fact(
                    "dependants[].specialNeeds",
                    eid,
                    f"{path}.specialNeeds",
                    "stated",
                    "client",
                    GoldValue(kind="boolean", label="true"),
                    True,
                )
            if d["monthlySupport"] and self.discuss("dependants[].monthlySupport"):
                obj = "him" if pron == "he" else "her"
                turns += self.amount_exchange(
                    "client",
                    f"How much do you give {first} each month?",
                    f"We send {obj} {{v}}.",
                    "dependants[].monthlySupport",
                    eid,
                    f"{path}.monthlySupport",
                    d["monthlySupport"],
                    "monthly",
                    allow_spouse=False,
                )
                if d["supportYears"] and self.discuss("dependants[].supportYears"):
                    fid = fact_id_for("dependants[].supportYears", eid)
                    yrs = int(d["supportYears"])
                    turns.append(T("advisor", "And for how long do you expect to keep supporting them?"))
                    turns.append(T("client_primary", "Probably for ", K(f"another {words_en(yrs)} years", fid), "."))
                    self.fact(
                        "dependants[].supportYears",
                        eid,
                        f"{path}.supportYears",
                        "stated",
                        "client",
                        GoldValue(kind="numeric", p50=yrs),
                        d["supportYears"],
                    )
            self.beat("dependants", turns)
            if rel == "child":
                self.education(d, eid, path, first, pron)

    def education(self, d: dict[str, Any], eid: str, path: str, first: str, pron: str) -> None:
        turns: list[BeatTurn] = []
        if d["schoolType"] != "none" and self.discuss("dependants[].schoolType"):
            fid = fact_id_for("dependants[].schoolType", eid)
            turns.append(T("advisor", f"Where is {first} at school?"))
            turns.append(
                T(
                    "client_primary",
                    f"{pron.capitalize()}'s at ",
                    K("a private school" if d["schoolType"] == "private" else "the government school", fid),
                    ".",
                )
            )
            self.fact(
                "dependants[].schoolType",
                eid,
                f"{path}.schoolType",
                "stated",
                "client",
                GoldValue(kind="categorical", label=d["schoolType"]),
                d["schoolType"],
            )
            if d["schoolFeesAnnual"] and self.discuss("dependants[].schoolFeesAnnual"):
                turns += self.amount_exchange(
                    "client",
                    "And the school fees?",
                    "The fees are {v}.",
                    "dependants[].schoolFeesAnnual",
                    eid,
                    f"{path}.schoolFeesAnnual",
                    d["schoolFeesAnnual"],
                    "annual",
                    unknown="what the fees are this year",
                )
        if age_on(d["dateOfBirth"], self.anchor) < 23 and self.discuss("dependants[].tertiary"):
            fid = fact_id_for("dependants[].tertiary", eid)
            turns.append(T("advisor", f"Is {first} planning to study after school?"))
            if d["tertiary"]:
                turns.append(T("client_primary", K(f"{pron.capitalize()} wants to go to varsity", fid), "."))
                gv = GoldValue(kind="boolean", label="true")
                self.fact("dependants[].tertiary", eid, f"{path}.tertiary", "stated", "client", gv, True)
                if self.discuss("dependants[].tertiaryStartAge"):
                    sfid = fact_id_for("dependants[].tertiaryStartAge", eid)
                    start = int(d["tertiaryStartAge"])
                    turns.append(T("client_primary", "Starting at ", K(say_age(start), sfid), ", after matric."))
                    self.fact(
                        "dependants[].tertiaryStartAge",
                        eid,
                        f"{path}.tertiaryStartAge",
                        "stated",
                        "client",
                        GoldValue(kind="numeric", p50=start),
                        start,
                    )
                if self.discuss("dependants[].tertiaryYears"):
                    yfid = fact_id_for("dependants[].tertiaryYears", eid)
                    yrs = int(d["tertiaryYears"])
                    turns.append(T("client_primary", "It's ", K(f"a {words_en(yrs)}-year degree", yfid), "."))
                    self.fact(
                        "dependants[].tertiaryYears",
                        eid,
                        f"{path}.tertiaryYears",
                        "stated",
                        "client",
                        GoldValue(kind="numeric", p50=yrs),
                        yrs,
                    )
                if self.discuss("dependants[].tertiaryCostAnnual"):
                    turns += self.amount_exchange(
                        "client",
                        "What do you think it will cost per year, in today's money?",
                        "It'll be {v}.",
                        "dependants[].tertiaryCostAnnual",
                        eid,
                        f"{path}.tertiaryCostAnnual",
                        d["tertiaryCostAnnual"],
                        "annual",
                        unknown="what varsity costs these days",
                    )
                if d["educationSavings"] and self.discuss("dependants[].educationSavings"):
                    turns += self.amount_exchange(
                        "client",
                        f"Have you saved anything for {first}'s studies yet?",
                        "We've got {v} saved so far.",
                        "dependants[].educationSavings",
                        eid,
                        f"{path}.educationSavings",
                        d["educationSavings"],
                        "once",
                    )
                if self.p(0.12):
                    ev = self.entity(f"event.study_{_slug(first)}", "goal", None, None, [f"{first} studying overseas", "studying overseas"])
                    ofid, kfid = fact_id_for("event[].occurs", ev), fact_id_for("event[].kind", ev)
                    word = _choice(self.rng, ("might", "probably", "maybe"))
                    lo, mid, hi = LIKELIHOOD[word]
                    turns.append(
                        T("client_primary", "And ", K(f"{pron} {word} study in the UK", ofid, kfid), ", which would be a lot more.")
                    )
                    self.fact("event[].occurs", ev, "", "stated", "client", GoldValue(kind="numeric", p10=lo, p50=mid, p90=hi, vague=True))
                    self.fact("event[].kind", ev, "", "stated", "client", GoldValue(kind="categorical", label="education-offshore"))
                    self.phenomena.append("probability_event")
            else:
                turns.append(T("client_primary", K(f"{pron.capitalize()}'s not interested in university", fid), "."))
                self.fact(
                    "dependants[].tertiary", eid, f"{path}.tertiary", "stated", "client", GoldValue(kind="boolean", label="false"), False
                )
        self.beat("education", turns)

    def income(self) -> None:
        for who in ("client", "spouse") if self.couple else ("client",):
            inc = self.data["income"][who]
            me = self.first_person(who)
            turns: list[BeatTurn] = []
            if inc["grossMonthly"] and self.discuss("income.{p}.grossMonthly"):
                q = self.you(who, "What do you earn before tax each month?") if me else f"And what does {self.name(who)} earn, gross?"
                s = "I earn {v} before tax." if me else (f"{self.name(who)} earns {{v}} before tax.")
                turns += self.amount_exchange(
                    who,
                    q,
                    s,
                    "income.{p}.grossMonthly",
                    who,
                    f"income.{who}.grossMonthly",
                    inc["grossMonthly"],
                    "monthly",
                    unknown="exactly, it changes with overtime",
                )
            if inc["annualBonus"] and self.discuss("income.{p}.annualBonus"):
                turns += self.amount_exchange(
                    who,
                    "Is there a bonus?",
                    "The bonus is {v}." if me else "There's a bonus of {v}.",
                    "income.{p}.annualBonus",
                    who,
                    f"income.{who}.annualBonus",
                    inc["annualBonus"],
                    "annual",
                )
            if who == "client" and self.data["client"]["employmentType"] in ("salaried", "commission") and self.p(0.08):
                ch = self.entity("income_change.promotion", "goal", None, None, ["promotion", "new role"])
                new = nice_amount(inc["grossMonthly"] * 1.25) if inc["grossMonthly"] else 50_000.0
                o, a, y = (
                    fact_id_for("income.{p}.expectedChange[].occurs", ch),
                    fact_id_for("income.{p}.expectedChange[].newGrossMonthly", ch),
                    fact_id_for("income.{p}.expectedChange[].year", ch),
                )
                turns.append(
                    T(
                        "client_primary",
                        "And ",
                        K("I'll probably get a promotion", o),
                        " ",
                        K("next year", y),
                        ", which should take me to ",
                        K(say_amount(new, "digits") + " a month", a),
                        ".",
                    )
                )
                self.fact(
                    "income.{p}.expectedChange[].occurs",
                    ch,
                    "",
                    "stated",
                    "client",
                    GoldValue(kind="numeric", p10=0.6, p50=0.7, p90=0.8, vague=True),
                )
                self.fact(
                    "income.{p}.expectedChange[].year", ch, "", "stated", "client", GoldValue(kind="numeric", p50=self.anchor.year + 1)
                )
                self.fact("income.{p}.expectedChange[].newGrossMonthly", ch, "", "stated", "client", GoldValue(kind="numeric", p50=new))
                self.phenomena.append("probability_event")
            self.beat("income", turns)

    def expenses(self) -> None:
        items = list(self.data["expenses"]["items"].items())
        self.rng.shuffle(items)  # type: ignore[arg-type]
        turns: list[BeatTurn] = [T("advisor", "Let's run through the monthly budget quickly.")]
        for key, amount in items[: int(self.rng.integers(3, 8))]:
            item = self.catalog.expense_item(key)
            if item is None or not amount:
                continue
            turns += self.amount_exchange(
                "client",
                f"What do you spend on {item.label.lower()}?",
                f"We spend {{v}} on {_decap(item.label)}.",
                "expenses.items[]",
                key,
                f"expenses.items[{key}]",
                amount,
                "monthly",
            )
        for c in self.data["expenses"]["custom"]:
            if not self.p(0.6):
                continue
            eid = self.entity(f"account.expense_{_slug(c['label'])}", "account", "expenses.custom", c["id"], [c["label"]])
            lf, af = fact_id_for("expenses.custom[].label", eid), fact_id_for("expenses.custom[].amount", eid)
            phrase, gv, phen = self.money(c["amount"], "monthly")
            turns.append(T("client_primary", "We also pay for ", K(c["label"].lower(), lf), ", that's ", K(phrase, af), "."))
            self.fact(
                "expenses.custom[].label",
                eid,
                f"expenses.custom[{c['id']}].label",
                "stated",
                "client",
                GoldValue(kind="text", label=c["label"]),
                c["label"],
            )
            self.fact("expenses.custom[].amount", eid, f"expenses.custom[{c['id']}].amount", "stated", "client", gv, c["amount"])
            self.phenomena.append(phen)
        self.beat("expenses", turns)

    def funds(self) -> None:
        for f in self.data["retirementFunds"]:
            if not self.p(0.85):
                continue
            who = f["owner"]
            me = self.first_person(who)
            spk = self.speaker(who)
            label = FUND_PHRASES[f["type"]]
            eid = self.entity(f"account.{_slug(f['type'])}_{who}", "account", "retirementFunds", f["id"], [f["name"], label, f["provider"]])
            path = f"retirementFunds[{f['id']}]"
            tf, of = fact_id_for("retirementFunds[].type", eid), fact_id_for("retirementFunds[].owner", eid)
            turns = [
                T(
                    "advisor",
                    self.you(who, "What retirement savings do you have?") if me else f"What retirement savings does {self.name(who)} have?",
                ),
                T(spk, "I've got " if me else f"{self.name(who)} has ", K(label, tf, of), "."),
            ]
            self.fact(
                "retirementFunds[].type", eid, f"{path}.type", "stated", "client", GoldValue(kind="categorical", label=f["type"]), f["type"]
            )
            self.fact("retirementFunds[].owner", eid, f"{path}.owner", "stated", "client", GoldValue(kind="categorical", label=who), who)
            if f["value"] and self.discuss("retirementFunds[].value"):
                turns += self.amount_exchange(
                    who,
                    "What's it worth at the moment?",
                    "It's worth {v}.",
                    "retirementFunds[].value",
                    eid,
                    f"{path}.value",
                    f["value"],
                    "once",
                    unknown="what it's worth, I haven't seen a statement",
                )
            if f["employeeMonthly"] and self.discuss("retirementFunds[].employeeMonthly"):
                turns += self.amount_exchange(
                    who,
                    "And how much goes in each month?",
                    "I put in {v}." if me else "They put in {v}.",
                    "retirementFunds[].employeeMonthly",
                    eid,
                    f"{path}.employeeMonthly",
                    f["employeeMonthly"],
                    "monthly",
                )
            if f["employerMonthly"] and self.discuss("retirementFunds[].employerMonthly"):
                turns += self.amount_exchange(
                    who,
                    "Does the company contribute as well?",
                    "The company adds {v}.",
                    "retirementFunds[].employerMonthly",
                    eid,
                    f"{path}.employerMonthly",
                    f["employerMonthly"],
                    "monthly",
                )
            if f["type"] == "living-annuity" and self.discuss("retirementFunds[].drawdownRate"):
                fid = fact_id_for("retirementFunds[].drawdownRate", eid)
                turns.append(T("advisor", "What are you drawing from it?"))
                turns.append(T(spk, "I draw ", K(say_percent(f["drawdownRate"]), fid), " a year."))
                self.fact(
                    "retirementFunds[].drawdownRate",
                    eid,
                    f"{path}.drawdownRate",
                    "stated",
                    "client",
                    GoldValue(kind="numeric", p50=f["drawdownRate"]),
                    f["drawdownRate"],
                )
            self.beat("retirement_funds", turns)

    def assets(self) -> None:
        for a in self.data["assets"]:
            if a["type"] not in ASSET_PHRASES or not self.p(0.8):
                continue
            who = "spouse" if a["owner"] == "spouse" else "client"
            spk = self.speaker(who)
            me = self.first_person(who)
            noun = a["description"] if a["type"] == "vehicle" else ASSET_PHRASES[a["type"]]
            eid = self.entity(
                f"asset.{_slug(a['type'])}_{_slug(a['description'])}",
                "property"
                if a["type"] in ("primary-residence", "residential-property", "vehicle")
                else ("business" if a["type"] == "business-interest" else "account"),
                "assets",
                a["id"],
                [a["description"], noun, a["provider"]],
            )
            path = f"assets[{a['id']}]"
            tf, of = fact_id_for("assets[].type", eid), fact_id_for("assets[].owner", eid)
            owner_word = "our" if a["owner"] == "joint" else ("my" if me else f"{self.name(who)}'s")
            turns = [T("advisor", "What else do you own?"), T(spk, "There's ", K(f"{owner_word} {noun}", tf, of), ".")]
            self.fact("assets[].type", eid, f"{path}.type", "stated", "client", GoldValue(kind="categorical", label=a["type"]), a["type"])
            self.fact(
                "assets[].owner", eid, f"{path}.owner", "stated", "client", GoldValue(kind="categorical", label=a["owner"]), a["owner"]
            )
            if a["value"] and self.discuss("assets[].value"):
                turns += self.amount_exchange(
                    who,
                    "What would you say it's worth?",
                    "It's worth {v}.",
                    "assets[].value",
                    eid,
                    f"{path}.value",
                    a["value"],
                    "once",
                    unknown="what it would sell for",
                )
            if a["earmark"] in EARMARK_PHRASES and self.discuss("assets[].earmark"):
                fid = fact_id_for("assets[].earmark", eid)
                turns.append(T(spk, "And ", K(EARMARK_PHRASES[a["earmark"]], fid), "."))
                self.fact(
                    "assets[].earmark",
                    eid,
                    f"{path}.earmark",
                    "stated",
                    "client",
                    GoldValue(kind="categorical", label=a["earmark"]),
                    a["earmark"],
                )
            if a["monthlyContribution"] and self.discuss("assets[].monthlyContribution"):
                turns += self.amount_exchange(
                    who,
                    "Are you still adding to it?",
                    "We add {v}.",
                    "assets[].monthlyContribution",
                    eid,
                    f"{path}.monthlyContribution",
                    a["monthlyContribution"],
                    "monthly",
                )
            self.beat("assets", turns)

    def liabilities(self) -> None:
        for li in self.data["liabilities"]:
            if not self.p(0.8):
                continue
            who = "spouse" if li["owner"] == "spouse" else "client"
            spk = self.speaker(who)
            eid = self.entity(
                f"liability.{_slug(li['type'])}_{_slug(li['description'])}",
                "liability",
                "liabilities",
                li["id"],
                [li["description"], LIABILITY_PHRASES[li["type"]], li["lender"]],
            )
            path = f"liabilities[{li['id']}]"
            tf, of = fact_id_for("liabilities[].type", eid), fact_id_for("liabilities[].owner", eid)
            owner_word = "our" if li["owner"] == "joint" else ("my" if self.first_person(who) else f"{self.name(who)}'s")
            turns = [
                T("advisor", "Any debt we should know about?"),
                T(spk, "There's ", K(f"{owner_word} {LIABILITY_PHRASES[li['type']].removeprefix('the ')}", tf, of), "."),
            ]
            self.fact(
                "liabilities[].type", eid, f"{path}.type", "stated", "client", GoldValue(kind="categorical", label=li["type"]), li["type"]
            )
            self.fact(
                "liabilities[].owner",
                eid,
                f"{path}.owner",
                "stated",
                "client",
                GoldValue(kind="categorical", label=li["owner"]),
                li["owner"],
            )
            if li["balance"] and self.discuss("liabilities[].balance"):
                turns += self.amount_exchange(
                    who,
                    "What's still outstanding?",
                    "It's sitting at {v}.",
                    "liabilities[].balance",
                    eid,
                    f"{path}.balance",
                    li["balance"],
                    "once",
                )
            if li["monthlyRepayment"] and self.discuss("liabilities[].monthlyRepayment"):
                turns += self.amount_exchange(
                    who,
                    "And the repayment?",
                    "We pay {v}.",
                    "liabilities[].monthlyRepayment",
                    eid,
                    f"{path}.monthlyRepayment",
                    li["monthlyRepayment"],
                    "monthly",
                )
            if self.discuss("liabilities[].interestRate"):
                fid = fact_id_for("liabilities[].interestRate", eid)
                turns.append(T(spk, "The rate is ", K(say_percent(li["interestRate"]), fid), "."))
                self.fact(
                    "liabilities[].interestRate",
                    eid,
                    f"{path}.interestRate",
                    "stated",
                    "client",
                    GoldValue(kind="numeric", p50=li["interestRate"]),
                    li["interestRate"],
                )
            self.beat("liabilities", turns)

    def policies(self) -> None:
        for po in self.data["policies"]:
            if not self.p(0.8):
                continue
            who = po["lifeAssured"]
            spk = self.speaker(who)
            me = self.first_person(who)
            eid = self.entity(
                f"policy.{_slug(po['insurer'])}_{_slug(po['type'])}_{who}",
                "policy",
                "policies",
                po["id"],
                [f"{po['insurer']} {po['type']}", POLICY_PHRASES[po["type"]], po["insurer"]],
            )
            path = f"policies[{po['id']}]"
            tf, lf, gf, inf = (
                fact_id_for("policies[].type", eid),
                fact_id_for("policies[].lifeAssured", eid),
                fact_id_for("policies[].isGroup", eid),
                fact_id_for("policies[].insurer", eid),
            )
            if po["isGroup"]:
                src: list[str | Piece] = [K("through work", gf)]
            else:
                src = ["with ", K(po["insurer"], gf, inf)]
                self.fact(
                    "policies[].insurer",
                    eid,
                    f"{path}.insurer",
                    "stated",
                    "client",
                    GoldValue(kind="text", label=po["insurer"]),
                    po["insurer"],
                )
            turns = [
                T(
                    "advisor",
                    self.you(who, "What cover do you have in place?") if me else f"What cover is there on {self.name(who)}'s life?",
                ),
                T(spk, "I've got " if me else f"{self.name(who)} has ", K(POLICY_PHRASES[po["type"]], tf, lf), " ", *src, "."),
            ]
            self.fact(
                "policies[].type", eid, f"{path}.type", "stated", "client", GoldValue(kind="categorical", label=po["type"]), po["type"]
            )
            self.fact(
                "policies[].lifeAssured", eid, f"{path}.lifeAssured", "stated", "client", GoldValue(kind="categorical", label=who), who
            )
            self.fact(
                "policies[].isGroup",
                eid,
                f"{path}.isGroup",
                "stated",
                "client",
                GoldValue(kind="boolean", label="true" if po["isGroup"] else "false"),
                po["isGroup"],
            )
            if po["cover"] and self.discuss("policies[].cover"):
                turns += self.amount_exchange(
                    who,
                    "How much is the cover?",
                    "The cover is {v}.",
                    "policies[].cover",
                    eid,
                    f"{path}.cover",
                    po["cover"],
                    "once",
                    unknown="what the cover amount is",
                )
            if po["monthlyBenefit"] and self.discuss("policies[].monthlyBenefit"):
                turns += self.amount_exchange(
                    who,
                    "What does it pay out per month?",
                    "It pays {v}.",
                    "policies[].monthlyBenefit",
                    eid,
                    f"{path}.monthlyBenefit",
                    po["monthlyBenefit"],
                    "monthly",
                )
            if po["premiumMonthly"] and self.discuss("policies[].premiumMonthly"):
                turns += self.amount_exchange(
                    who,
                    "And the premium?",
                    "It costs {v}.",
                    "policies[].premiumMonthly",
                    eid,
                    f"{path}.premiumMonthly",
                    po["premiumMonthly"],
                    "monthly",
                )
            self.beat("policies", turns)

    def retirement(self) -> None:
        for who in ("client", "spouse") if self.couple else ("client",):
            g = self.data["retirement"][who]
            if self.data[who]["employmentType"] == "retired":
                continue
            spk, me = self.speaker(who), self.first_person(who)
            turns: list[BeatTurn] = []
            ra = int(g["retirementAge"])
            if self.discuss("retirement.{p}.retirementAge"):
                fid = fact_id_for("retirement.{p}.retirementAge", who)
                path = f"retirement.{who}.retirementAge"
                r = self.rng.random()
                dob = self.data[who]["dateOfBirth"]
                age = age_on(dob, self.anchor) if dob else None
                band = next((b for b, (lo, _, hi) in AGE_BANDS.items() if lo < ra < hi and ra != 57), None)
                if r < 0.15 * self.rate:
                    turns.append(T("advisor", "Most people your age plan for ", K("sixty-five", fid), ". Shall we use that?"))
                    if self.p(0.6):
                        turns.append(T(spk, K("Ja, sixty-five is fine", fid), "."))
                        self.fact(
                            "retirement.{p}.retirementAge",
                            who,
                            path,
                            "stated",
                            "advisor_confirmed_by_client",
                            GoldValue(kind="numeric", p50=65),
                            65,
                        )
                        self.phenomena.append("advisor_confirmed")
                    else:
                        turns.append(T(spk, "Hmm, I'll have to think about that."))
                        self.fact(
                            "retirement.{p}.retirementAge", who, path, "stated", "advisor_suggested", GoldValue(kind="numeric", p50=65), 65
                        )
                        self.phenomena.append("advisor_suggested")
                elif r < 0.35 * self.rate and age is not None and ra > age:
                    exact = (self.anchor - date.fromisoformat(dob)).days / 365.25
                    n = ra - age
                    turns.append(
                        T(
                            "advisor",
                            self.you(who, "When do you want to stop working?") if me else f"When does {self.name(who)} want to retire?",
                        )
                    )
                    turns.append(T(spk, "In ", K(f"about {words_en(n)} years", fid), " or so."))
                    mid = exact + n
                    self.fact(
                        "retirement.{p}.retirementAge",
                        who,
                        path,
                        "implied",
                        "client",
                        GoldValue(kind="numeric", p10=mid - 1.5, p50=mid, p90=mid + 1.5, vague=True),
                        ra,
                    )
                    self.phenomena.append("implied")
                elif r < 0.5 * self.rate and band:
                    lo, mode, hi = AGE_BANDS[band]
                    tri = stats.summary(Triangular(low=lo, mode=mode, high=hi))
                    p10, p90 = tri["p10"], tri["p90"]
                    turns.append(
                        T(
                            "advisor",
                            self.you(who, "When do you want to stop working?") if me else f"When does {self.name(who)} want to retire?",
                        )
                    )
                    turns.append(
                        T(spk, "Somewhere in my " if me else f"Somewhere in {'her' if self.spouse_she else 'his'} ", K(band, fid), ".")
                    )
                    self.fact(
                        "retirement.{p}.retirementAge",
                        who,
                        path,
                        "stated",
                        "client",
                        GoldValue(kind="numeric", p10=min(p10, ra), p50=ra, p90=max(p90, ra), vague=True),
                        ra,
                    )
                    self.phenomena.append("vague_age")
                else:
                    turns.append(
                        T(
                            "advisor",
                            self.you(who, "When do you want to stop working?") if me else f"When does {self.name(who)} want to retire?",
                        )
                    )
                    turns.append(T(spk, "I'd like to retire at " if me else "Retire at ", K(say_age(ra), fid), "."))
                    self.fact("retirement.{p}.retirementAge", who, path, "stated", "client", GoldValue(kind="numeric", p50=ra), ra)
            if self.discuss("retirement.{p}.targetMode"):
                mf = fact_id_for("retirement.{p}.targetMode", who)
                turns.append(T("advisor", "What income would you want in retirement?"))
                if g["targetMode"] == "percent":
                    pf = fact_id_for("retirement.{p}.targetPercent", who)
                    turns.append(T(spk, "About ", K(say_percent(g["targetPercent"]), pf), " of ", K("what I earn now", mf), "."))
                    h = 0.10 * TRI_Q
                    self.fact(
                        "retirement.{p}.targetPercent",
                        who,
                        f"retirement.{who}.targetPercent",
                        "stated",
                        "client",
                        GoldValue(
                            kind="numeric",
                            p10=g["targetPercent"] * (1 - h),
                            p50=g["targetPercent"],
                            p90=g["targetPercent"] * (1 + h),
                            vague=True,
                        ),
                        g["targetPercent"],
                    )
                    self.fact(
                        "retirement.{p}.targetMode",
                        who,
                        f"retirement.{who}.targetMode",
                        "stated",
                        "client",
                        GoldValue(kind="categorical", label="percent"),
                        "percent",
                    )
                else:
                    af = fact_id_for("retirement.{p}.targetMonthly", who)
                    phrase, gv, phen = self.money(g["targetMonthly"], "monthly")
                    turns.append(T(spk, "I'd need ", K(phrase, af), " ", K("in today's money", mf), "."))
                    self.fact(
                        "retirement.{p}.targetMonthly", who, f"retirement.{who}.targetMonthly", "stated", "client", gv, g["targetMonthly"]
                    )
                    self.fact(
                        "retirement.{p}.targetMode",
                        who,
                        f"retirement.{who}.targetMode",
                        "stated",
                        "client",
                        GoldValue(kind="categorical", label="amount"),
                        "amount",
                    )
                    self.phenomena.append(phen)
            self.beat("retirement", turns)

    def goals(self) -> None:
        for gl in self.data["goals"]:
            if not self.p(0.85):
                continue
            eid = self.entity(f"goal.{_slug(gl['name'])}", "goal", "goals", gl["id"], [gl["name"]])
            path = f"goals[{gl['id']}]"
            nf, cf, yf = fact_id_for("goals[].name", eid), fact_id_for("goals[].cost", eid), fact_id_for("goals[].targetYear", eid)
            phrase, gv, phen = self.money(gl["cost"])
            years = gl["targetYear"] - self.anchor.year
            when = (
                K(f"in {gl['targetYear']}", yf) if self.p(0.6) else K(f"in {words_en(years)} years' time" if years > 1 else "next year", yf)
            )
            turns = [
                T("advisor", "Are you saving for anything specific?"),
                T("client_primary", "We're planning a ", K(gl["name"].lower(), nf), " ", when, ", it'll cost ", K(phrase, cf), "."),
            ]
            self.fact("goals[].name", eid, f"{path}.name", "stated", "client", GoldValue(kind="text", label=gl["name"]), gl["name"])
            self.fact(
                "goals[].targetYear",
                eid,
                f"{path}.targetYear",
                "stated",
                "client",
                GoldValue(kind="numeric", p50=gl["targetYear"]),
                gl["targetYear"],
            )
            self.fact("goals[].cost", eid, f"{path}.cost", "stated", "client", gv, gl["cost"])
            self.phenomena.append(phen)
            if gl["existingSavings"] and self.discuss("goals[].existingSavings"):
                turns += self.amount_exchange(
                    "client",
                    "Have you saved anything towards it?",
                    "We've got {v} so far.",
                    "goals[].existingSavings",
                    eid,
                    f"{path}.existingSavings",
                    gl["existingSavings"],
                    "once",
                )
            if gl["monthlyContribution"] and self.discuss("goals[].monthlyContribution"):
                turns += self.amount_exchange(
                    "client",
                    "And each month?",
                    "We put away {v}.",
                    "goals[].monthlyContribution",
                    eid,
                    f"{path}.monthlyContribution",
                    gl["monthlyContribution"],
                    "monthly",
                )
            self.beat("goals", turns)

    def estate(self) -> None:
        for who in ("client", "spouse") if self.couple else ("client",):
            e = self.data["estate"][who]
            spk, me = self.speaker(who), self.first_person(who)
            turns: list[BeatTurn] = []
            if self.discuss("estate.{p}.hasWill"):
                fid = fact_id_for("estate.{p}.hasWill", who)
                turns.append(T("advisor", self.you(who, "Do you have a will?") if me else f"Does {self.name(who)} have a will?"))
                text = (
                    WILL_PHRASES[e["hasWill"]]
                    if me
                    else WILL_PHRASES[e["hasWill"]]
                    .replace("I've got", f"{self.name(who)} has")
                    .replace("I don't have", f"{self.name(who)} doesn't have")
                    .replace("I'm not sure if I have", "we're not sure if there's")
                )
                turns.append(T(spk, K(text, fid), "."))
                self.fact(
                    "estate.{p}.hasWill",
                    who,
                    f"estate.{who}.hasWill",
                    "stated",
                    "client",
                    GoldValue(kind="categorical", label=e["hasWill"]),
                    e["hasWill"],
                )
            if who == "client" and e["guardianNominated"] in GUARDIAN_PHRASES and self.discuss("estate.{p}.guardianNominated"):
                fid = fact_id_for("estate.{p}.guardianNominated", who)
                turns.append(T("client_primary", "And ", K(GUARDIAN_PHRASES[e["guardianNominated"]], fid), "."))
                self.fact(
                    "estate.{p}.guardianNominated",
                    who,
                    f"estate.{who}.guardianNominated",
                    "stated",
                    "client",
                    GoldValue(kind="categorical", label=e["guardianNominated"]),
                    e["guardianNominated"],
                )
            if who == "client" and self.discuss("estate.{p}.funeralCost"):
                fid = fact_id_for("estate.{p}.funeralCost", who)
                cost = e["funeralCost"]
                turns.append(
                    T(
                        "advisor",
                        "For the funeral, families usually budget about ",
                        K(say_amount(cost, "digits"), fid),
                        ". Does that sound right?",
                    )
                )
                if self.p(0.7):
                    turns.append(T("client_primary", K("Ja, that sounds right", fid), "."))
                    self.fact(
                        "estate.{p}.funeralCost",
                        who,
                        f"estate.{who}.funeralCost",
                        "stated",
                        "advisor_confirmed_by_client",
                        GoldValue(kind="numeric", p50=cost),
                        cost,
                    )
                    self.phenomena.append("advisor_confirmed")
                else:
                    turns.append(T("client_primary", "I really don't know what funerals cost."))
                    self.fact(
                        "estate.{p}.funeralCost",
                        who,
                        f"estate.{who}.funeralCost",
                        "stated",
                        "advisor_suggested",
                        GoldValue(kind="numeric", p50=cost),
                        cost,
                    )
                    self.phenomena.append("advisor_suggested")
            self.beat("estate", turns)

    def risk(self) -> None:
        answers = self.data["riskProfile"]["answers"]
        turns: list[BeatTurn] = []
        for q in self.catalog.risk_questions:
            if q.id not in ("horizon", "drawdown", "tradeoff") or q.id not in answers or not self.p(0.5):
                continue
            slot_id = f"riskProfile.answers[{q.id}]"
            label = q.options[int(answers[q.id])]
            fid = fact_id_for(slot_id, None)
            turns.append(T("advisor", q.question))
            turns.append(T("client_primary", "I'd say ", K(_decap(label), fid), "."))
            self.fact(slot_id, None, slot_id, "stated", "client", GoldValue(kind="categorical", label=label), int(answers[q.id]))
        self.beat("risk_profile", turns)

    def engagement(self) -> None:
        eng = self.data["engagement"]
        if eng["clientConcerns"] and self.p(0.5):
            fid = fact_id_for("engagement.clientConcerns", None)
            text = eng["clientConcerns"].rstrip(".")
            self.beat(
                "engagement",
                [
                    T("advisor", "Is anything worrying you about your finances?"),
                    T("client_primary", "Honestly, we're ", K(_decap(text), fid), "."),
                ],
            )
            self.fact(
                "engagement.clientConcerns",
                None,
                "engagement.clientConcerns",
                "stated",
                "client",
                GoldValue(kind="text", label=text),
                eng["clientConcerns"],
            )

    def closing(self) -> None:
        self.beat(
            "closing",
            [
                T("advisor", "Thank you, that's everything I need for now. I'll send the follow-up questions by email."),
                T("client_primary", "Thanks, speak soon."),
            ],
        )

    def off_topic(self) -> None:
        n = int(self.rng.integers(2, 5))
        for _ in range(n):
            pos = int(self.rng.integers(1, max(2, len(self.beats))))
            spk = _choice(
                self.rng, ("client_primary", "client_primary", "advisor") + (("client_secondary",) if self.spouse_present else ())
            )
            self.beats.insert(pos, Beat(topic="off_topic", turns=[T(spk, _choice(self.rng, OFF_TOPIC))]))
            self.phenomena.append("off_topic")

    def not_discussed(self) -> None:
        """Gold not_discussed entries for tier A household and per-person slots the call never touched."""
        persons = ("client", "spouse") if self.couple else ("client",)
        for slot in self.catalog.tier_a():
            if slot.cardinality not in ("one", "per_person") or not slot.exported:
                continue
            for inst in persons if slot.per_person else (None,):
                fid = fact_id_for(slot.slot_id, inst)
                if fid not in self.facts:
                    self.fact(slot.slot_id, inst, slot.fna_path_for(inst), "not_discussed")

    def run(self) -> None:
        self.opening()
        self.people()
        self.household()
        self.dependants()
        self.income()
        self.expenses()
        self.funds()
        self.assets()
        self.liabilities()
        self.policies()
        self.retirement()
        self.goals()
        self.estate()
        self.risk()
        self.engagement()
        self.closing()
        self.off_topic()
        self.not_discussed()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:24] or "item"


_STARTERS = frozenset({"The", "It's", "It'll", "It", "We", "We've", "We're", "They", "There's", "Our", "My", "Retire"})


def _lower_first(s: str) -> str:
    """Lower-case a sentence start after a hedge ('The fees are' → 'the fees are'), leaving 'I' and names alone."""
    word = s.split(" ", 1)[0]
    return s[0].lower() + s[1:] if word in _STARTERS else s


# --------------------------------------------------------------------------- scripts: template and LLM


def render_template(beats: Sequence[Beat]) -> tuple[list[ScriptTurn], dict[str, list[tuple[int, int, int]]]]:
    """The deterministic script: each beat turn verbatim. Returns turns and, per fact id, (turn, start, end) spans."""
    turns: list[ScriptTurn] = []
    spans: dict[str, list[tuple[int, int, int]]] = {}
    for b in beats:
        for bt in b.turns:
            pos = 0
            for p in bt.pieces:
                for k in p.keys:
                    spans.setdefault(k, []).append((len(turns), pos, pos + len(p.text)))
                pos += len(p.text)
            turns.append(ScriptTurn(bt.speaker, bt.text))
    return turns, spans


def required_phrases(beats: Sequence[Beat]) -> list[tuple[str, str, list[str]]]:
    """Every keyed phrase in order: (speaker, phrase, fact ids)."""
    return [(bt.speaker, p.text, p.keys) for b in beats for bt in b.turns for p in bt.pieces if p.keys]


def locate_phrases(turns: Sequence[ScriptTurn], beats: Sequence[Beat]) -> tuple[dict[str, list[tuple[int, int, int]]], list[str]]:
    """Find each keyed phrase, in order, in a turn by the same speaker. Returns spans and the phrases not found."""
    spans: dict[str, list[tuple[int, int, int]]] = {}
    missing = []
    ti, offset = 0, 0
    for speaker, phrase, keys in required_phrases(beats):
        found = None
        for j in range(ti, len(turns)):
            if turns[j].speaker != speaker:
                continue
            start = turns[j].text.find(phrase, offset if j == ti else 0)
            if start >= 0:
                found = (j, start)
                break
        if found is None:
            missing.append(f"{speaker}: {phrase!r}")
            continue
        j, start = found
        for k in keys:
            spans.setdefault(k, []).append((j, start, start + len(phrase)))
        ti, offset = j, start + len(phrase)
    return spans, missing


class DraftTurn(BaseModel):
    speaker: Literal["advisor", "client_primary", "client_secondary"]
    text: str


class ScriptDraft(BaseModel):
    """The LLM writer's output (strict-friendly: every field required, no dict-valued fields)."""

    turns: list[DraftTurn]


@dataclass
class WriterDeps:
    beats: list[Beat]
    brief: str = ""
    problems: list[str] = field(default_factory=list)


SCRIPT_WRITER_INSTRUCTIONS = """\
You write realistic transcripts of South African financial adviser calls, used as synthetic evaluation data.
You get an outline: beats of an advice call, each with turns marked by speaker. Phrases inside ⟦ ⟧ are evidence
for the evaluation and must appear verbatim (without the brackets), in a turn by the same speaker, in the same
order as in the outline.

Rules:
- Rewrite everything outside ⟦ ⟧ into natural, varied conversation: interruptions, filler ("ja", "hey", "shame"),
  small talk, the adviser checking understanding. Keep the beat order.
- Do not add, change or drop any amount, age, percentage, date or yes/no answer outside the marked phrases, and
  do not repeat a marked phrase elsewhere.
- Speakers: "advisor", "client_primary", "client_secondary" (the spouse, only if the outline uses it).
- Return every turn of the call.
"""


def script_writer_agent(pins: VersionPins | None = None) -> Any:
    """The LLM script writer: a Pydantic AI agent on the frontier model with ``NativeOutput(ScriptDraft)``."""
    from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext

    from ..llm import for_role

    rm = for_role("frontier", pins)
    agent = Agent(
        rm.model,
        name="synthetic_script_writer",
        deps_type=WriterDeps,
        output_type=NativeOutput(ScriptDraft),
        instructions=SCRIPT_WRITER_INSTRUCTIONS,
        model_settings=rm.settings,
        retries=3,
    )

    @agent.output_validator
    def _keep_phrases(ctx: RunContext[WriterDeps], out: ScriptDraft) -> ScriptDraft:
        turns = [ScriptTurn(t.speaker, t.text) for t in out.turns]
        _, missing = locate_phrases(turns, ctx.deps.beats)
        if missing:
            raise ModelRetry(
                "These marked phrases are missing, changed, out of order or in the wrong speaker's turn; include each verbatim:\n- "
                + "\n- ".join(missing[:30])
            )
        return out

    return agent


def outline(beats: Sequence[Beat], brief: str = "") -> str:
    lines = [brief.strip(), ""] if brief else []
    for i, b in enumerate(beats, 1):
        lines.append(f"Beat {i} ({b.topic}):")
        for bt in b.turns:
            lines.append(f"  {bt.speaker}: " + "".join(f"⟦{p.text}⟧" if p.keys else p.text for p in bt.pieces))
    return "\n".join(lines)


def write_script_llm(beats: Sequence[Beat], *, agent: Any = None, pins: VersionPins | None = None, brief: str = "") -> list[ScriptTurn]:
    """The LLM path: the writer rewrites the outline; the output validator retries until every keyed phrase survives."""
    agent = agent or script_writer_agent(pins)
    deps = WriterDeps(beats=list(beats), brief=brief)
    result = call(agent.run, outline(beats, brief), deps=deps)
    return [ScriptTurn(t.speaker, t.text) for t in result.output.turns]


# --------------------------------------------------------------------------- transcripts


SPEAKER_LABELS = {"advisor": "spk_0", "client_primary": "spk_1", "client_secondary": "spk_2", "other": "spk_3"}


def fallback_transcript(
    turns: Sequence[ScriptTurn], anchor: date, call_id: str, *, words_per_second: float = 2.6, gap_s: float = 0.6
) -> Transcript:
    """One segment per turn with even word timings: the offline stand-in for ``ingest.transcript_from_script``."""
    t = 0.0
    segments = []
    for i, (speaker, text) in enumerate(turns, start=1):
        toks = text.split()
        words = []
        start = t
        for w in toks:
            dur = max(0.15, len(w) / (words_per_second * 5.0))
            words.append(Word(text=w, t_start=round(t, 3), t_end=round(t + dur, 3), conf=0.97))
            t += dur + 0.05
        end = max(t, start + 0.2)
        segments.append(
            Segment(
                segment_id=f"s{i:04d}",
                speaker_label=SPEAKER_LABELS.get(speaker, "spk_3"),
                speaker_role=speaker if speaker in SPEAKER_LABELS else "other",
                role_conf=1.0,
                diarization_conf=1.0,
                t_start=round(start, 3),
                t_end=round(end, 3),
                words=words,
                text=text,
            )
        )
        t = end + gap_s
    return Transcript(call_id=call_id, anchor_date=anchor, segments=segments, asr_provider="script", asr_model_version="script-1")


def script_transcript(turns: Sequence[ScriptTurn], anchor: date, call_id: str) -> Transcript:
    """The script as a Transcript: ``advisor_intel.ingest.transcript_from_script`` when installed, else the fallback."""
    try:
        from ..ingest import transcript_from_script  # type: ignore[attr-defined]
    except ImportError:
        return fallback_transcript(turns, anchor, call_id)
    for kwargs in ({"call_id": call_id}, {}):
        try:
            t = transcript_from_script(list(turns), anchor, **kwargs)
            break
        except TypeError:
            continue
    else:
        return fallback_transcript(turns, anchor, call_id)
    return t if t.call_id == call_id else t.model_copy(update={"call_id": call_id})


def spans_on_transcript(
    spans: dict[str, list[tuple[int, int, int]]], turns: Sequence[ScriptTurn], transcript: Transcript, *, transcript_kind: str = "script"
) -> dict[str, list[Span]]:
    """Map (turn, start, end) spans onto transcript segments by locating each quote (segments may split turns)."""
    one_to_one = len(transcript.segments) == len(turns) and all(s.text == t.text for s, t in zip(transcript.segments, turns))
    out: dict[str, list[Span]] = {}
    for fid, items in spans.items():
        for ti, a, b in items:
            quote = turns[ti].text[a:b]
            if one_to_one:
                seg, start = transcript.segments[ti], a
            else:
                hit = next(
                    ((s, s.text.find(quote)) for s in transcript.segments if s.speaker_role == turns[ti].speaker and quote in s.text), None
                )
                if hit is None:
                    continue
                seg, start = hit
            n = max(1, len(seg.text))
            t0 = seg.t_start + (seg.t_end - seg.t_start) * start / n
            t1 = seg.t_start + (seg.t_end - seg.t_start) * (start + len(quote)) / n
            out.setdefault(fid, []).append(
                Span(
                    segment_id=seg.segment_id,
                    char_start=start,
                    char_end=start + len(quote),
                    quote=quote,
                    t_start=round(t0, 3),
                    t_end=round(t1, 3),
                    transcript=transcript_kind,
                )
            )
    return out


# --------------------------------------------------------------------------- whole calls


def _prior_from(rng: Generator, household: F.FnaDocument, plan: _Planner, *, new_client: bool) -> F.FnaDocument | None:
    """The client's FNA before the call: the household with some discussed values stale and some discussed rows missing."""
    if new_client:
        return None
    data = household.model_dump(mode="json")
    for path, raw in plan.writes.items():
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw and rng.random() < 0.4:
            stale = nice_amount(raw * float(_choice(rng, (0.8, 0.9, 1.1)))) if abs(raw) > 100 else raw
            try:
                set_path(data, path, stale)
            except (KeyError, TypeError):
                pass
    for lst, row in sorted(plan.discussed_rows):
        if rng.random() < 0.25:
            container = data
            for part in lst.split("."):
                container = container[part]
            container[:] = [r for r in container if r.get("id") != row]
    data["updatedAt"] = data["createdAt"]
    return F.FnaDocument.model_validate(data)


def _gold_document(prior: F.FnaDocument | None, writes: dict[str, Any], anchor: date, ns: str) -> F.FnaDocument:
    """The prior updated with every value the call established (what a perfect review exports)."""
    base = (
        prior
        if prior is not None
        else F.FnaDocument(id=_uid(ns, "doc"), createdAt=f"{anchor.isoformat()}T08:00:00Z", updatedAt=f"{anchor.isoformat()}T08:00:00Z")
    )
    data = base.model_dump(mode="json")
    data["engagement"]["meetingDate"] = anchor.isoformat()
    for path, raw in sorted(writes.items()):
        try:
            set_path(data, path, raw)
        except (KeyError, TypeError):
            continue
    return F.FnaDocument.model_validate(data)


def _entity_map(prior: F.FnaDocument | None, entities: dict[str, GoldEntity], call_id: str, anchor: date) -> dict[str, str]:
    """Gold entity id → the registry id ``load_prior`` gives the same FnaDocument row."""
    if prior is None:
        return {}
    profile = load_prior(prior, call_id=call_id, run_id="gold", anchor_date=anchor)
    by_row = {e.fna_id: eid for eid, e in profile.entities.items() if e.fna_id}
    return {g.entity_id: by_row[g.fna_id] for g in entities.values() if g.fna_id and g.fna_id in by_row}


def generate_call(
    index: int,
    *,
    seed: int = 0,
    archetype: str | None = None,
    writer: Literal["template", "llm"] = "template",
    agent: Any = None,
    pins: VersionPins | None = None,
    seed_doc: F.FnaDocument | None = None,
    anchor: date | None = None,
    catalog: Catalog | None = None,
    phenomenon_rate: float = 1.0,
) -> SyntheticCall:
    """One synthetic call, fully determined by (seed, index) on the template path."""
    catalog = catalog or load_catalog()
    rng = np.random.default_rng([seed, index])
    archetype = archetype or ARCHETYPES[index % len(ARCHETYPES)]
    anchor = anchor or (DEFAULT_ANCHOR + timedelta(days=int(rng.integers(0, 60))))
    call_id = f"syn-{seed}-{index:04d}"
    ns = call_id
    household = sample_household(rng, anchor, archetype, ns=ns, seed_doc=seed_doc)
    plan = _Planner(household, rng, anchor, catalog, phenomenon_rate=phenomenon_rate)
    plan.run()
    prior = _prior_from(rng, household, plan, new_client=bool(rng.random() < 0.2))
    if writer == "llm":
        names = ", ".join(n for n in (plan.client_name, plan.spouse_name if plan.spouse_present else "") if n)
        script = write_script_llm(plan.beats, agent=agent, pins=pins, brief=f"Household: {names}. Call date {anchor.isoformat()}.")
        spans, missing = locate_phrases(script, plan.beats)
        if missing:
            raise ValueError(f"the script writer dropped {len(missing)} keyed phrases: {missing[:3]}")
    else:
        script, spans = render_template(plan.beats)
    transcript = script_transcript(script, anchor, call_id)
    seg_spans = spans_on_transcript(spans, script, transcript)
    facts = [f.model_copy(update={"spans": seg_spans.get(fid, [])}) for fid, f in sorted(plan.facts.items())]
    document = _gold_document(prior, plan.writes, anchor, ns)
    gold = GoldCall(
        call_id=call_id,
        client_id=f"syn-client-{seed}-{index:04d}",
        anchor_date=anchor,
        source="synthetic",
        tags=[SYNTHETIC_TAG, archetype],
        transcript="script",
        document=document,
        prior_document=prior,
        facts=facts,
        entities=sorted(plan.entities.values(), key=lambda e: e.entity_id),
        entity_map=_entity_map(prior, plan.entities, call_id, anchor),
        annotators=["synthetic-generator"],
    )
    return SyntheticCall(
        call_id=call_id,
        client_id=gold.client_id,
        index=index,
        seed=seed,
        anchor_date=anchor,
        archetype=archetype,
        writer=writer,
        household=household,
        prior_document=prior,
        document=document,
        script=script,
        gold=gold,
        phenomena=sorted(set(plan.phenomena)),
        tags=[SYNTHETIC_TAG, archetype],
    )


def generate_calls(n: int, *, seed: int = 0, start: int = 0, **kwargs: Any) -> list[SyntheticCall]:
    """``n`` synthetic calls (§15.1 sizes the v1 set at 300), cycling through the archetypes."""
    return [generate_call(i, seed=seed, **kwargs) for i in range(start, start + n)]


def gold_value_for(slot_id: str, raw: Any, catalog: Catalog | None = None) -> GoldValue | None:
    """A point gold value for a raw FnaDocument value (convenience for hand-written gold calls)."""
    return gold_value((catalog or load_catalog()).slot(slot_id), value=raw)


# --------------------------------------------------------------------------- voice rendering


class TTSProvider(Protocol):
    """A text-to-speech vendor for rendering synthetic scripts (voice actors are the alternative, §15.1).

    ``synthesize`` returns mono PCM16 little-endian audio at ``sample_rate``."""

    sample_rate: int

    def synthesize(self, text: str, *, voice: str, language: str = "en-ZA") -> bytes: ...


class RenderedAudio(BaseModel):
    pcm: bytes
    sample_rate: int
    turn_times: list[tuple[float, float]]


DEFAULT_VOICES = {"advisor": "advisor-1", "client_primary": "client-1", "client_secondary": "client-2", "other": "other-1"}


def render_audio(
    turns: Sequence[ScriptTurn],
    tts: TTSProvider,
    *,
    voices: dict[str, str] | None = None,
    gap_s: float = 0.35,
    snr_db: float | None = 25.0,
    seed: int = 0,
) -> RenderedAudio:
    """Render a script turn by turn with gaps between speakers, then add white noise at ``snr_db`` (None: clean)."""
    voices = voices or DEFAULT_VOICES
    sr = tts.sample_rate
    gap = np.zeros(int(gap_s * sr), dtype=np.int16)
    chunks, times, t = [], [], 0.0
    for speaker, text in turns:
        audio = np.frombuffer(tts.synthesize(text, voice=voices.get(speaker, voices.get("other", "other-1"))), dtype="<i2")
        times.append((round(t, 3), round(t + len(audio) / sr, 3)))
        chunks += [audio, gap]
        t += (len(audio) + len(gap)) / sr
    signal = np.concatenate(chunks).astype(np.float64) if chunks else np.zeros(0)
    if snr_db is not None and signal.size:
        power = float(np.mean(signal**2)) or 1.0
        noise = np.random.default_rng(seed).normal(0.0, math.sqrt(power / 10 ** (snr_db / 10)), signal.size)
        signal = signal + noise
    pcm = np.clip(np.round(signal), -32768, 32767).astype("<i2").tobytes()
    return RenderedAudio(pcm=pcm, sample_rate=sr, turn_times=times)
