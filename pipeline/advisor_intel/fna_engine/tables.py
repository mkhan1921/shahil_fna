"""Shared SARS tables (§12.7): one schema-validated JSON file per tax year in ``fna_tables/``.

``lib/fna/tax.ts`` imports the same files, so neither engine hard-codes a SARS number. Years after the
latest file are projected with :func:`indexed_table` (§12.3).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import cached_property, lru_cache
from itertools import pairwise
from pathlib import Path
from typing import Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..models.fna import DEFAULT_TAX_YEAR

__all__ = [
    "Bracket",
    "TableError",
    "TaxTable",
    "TaxTableFile",
    "available_tax_years",
    "get_table",
    "indexed_table",
    "latest_tax_year",
    "table_for",
    "table_for_year",
]

TS_TABLE_FIELDS = (
    "label",
    "period",
    "endDate",
    "status",
    "brackets",
    "rebates",
    "thresholds",
    "medicalCredits",
    "retirementDeduction",
    "cgt",
    "tfsa",
    "uif",
    "interestExemption",
    "estateDuty",
    "retirementLumpSum",
    "withdrawalLumpSum",
    "sources",
)


class TableError(ValueError):
    """A table file that fails the schema or the consistency checks."""


class _Part(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Bracket(_Part):
    """One bracket of a progressive table; ``base`` is the tax on an amount equal to ``from``."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)
    from_: float = Field(alias="from")
    base: float
    rate: float


class Rebates(_Part):
    primary: float
    secondary: float
    tertiary: float


class Thresholds(_Part):
    under65: float
    age65to74: float
    age75plus: float


class MedicalCredits(_Part):
    main: float
    firstDependant: float
    additional: float


class RetirementDeduction(_Part):
    rate: float
    cap: float


class Cgt(_Part):
    inclusionRate: float
    annualExclusion: float
    deathExclusion: float
    primaryResidence: float


class Tfsa(_Part):
    annual: float
    lifetime: float


class Uif(_Part):
    rate: float
    ceilingMonthly: float


class InterestExemption(_Part):
    under65: float
    over65: float


class EstateDutyRates(_Part):
    abatement: float
    rateLow: float
    threshold: float
    rateHigh: float


class Donations(_Part):
    annualExemption: float
    rateLow: float
    threshold: float
    rateHigh: float
    source: str


class SavingsWithdrawal(_Part):
    perTaxYear: int
    minimum: float
    source: str


class LivingAnnuity(_Part):
    minDrawdown: float
    maxDrawdown: float
    source: str


class _Fast:
    """Plain tuples of the hot fields, built once per table for the tax functions."""

    __slots__ = ("bases", "froms", "np", "rates", "ret_bases", "ret_froms", "ret_rates", "wd_bases", "wd_froms", "wd_rates")

    def __init__(self, t: TaxTable):
        self.froms, self.bases, self.rates = _columns(t.brackets)
        self.ret_froms, self.ret_bases, self.ret_rates = _columns(t.retirementLumpSum)
        self.wd_froms, self.wd_bases, self.wd_rates = _columns(t.withdrawalLumpSum)
        self.np = None  # numpy arrays, built on first vectorised use


def _columns(brackets: tuple[Bracket, ...]) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    return tuple(b.from_ for b in brackets), tuple(b.base for b in brackets), tuple(b.rate for b in brackets)


class TaxTable(_Part):
    """The TypeScript ``TaxTable`` (lib/fna/tax.ts): the part of a table file that ``Analysis.table`` reports."""

    label: str
    period: str
    endDate: str  # last day of the year of assessment; age for the rebates is measured on it
    status: Literal["confirmed", "provisional"]
    brackets: tuple[Bracket, ...]
    rebates: Rebates
    thresholds: Thresholds
    medicalCredits: MedicalCredits
    retirementDeduction: RetirementDeduction
    cgt: Cgt
    tfsa: Tfsa
    uif: Uif
    interestExemption: InterestExemption
    estateDuty: EstateDutyRates
    retirementLumpSum: tuple[Bracket, ...]
    withdrawalLumpSum: tuple[Bracket, ...]
    sources: tuple[str, ...]

    @cached_property
    def fast(self) -> _Fast:
        return _Fast(self)

    @cached_property
    def end_date(self) -> date:
        return date.fromisoformat(self.endDate)


class TaxTableFile(TaxTable):
    """A whole ``fna_tables/za_<year>.json`` file (``TaxTableFile`` in lib/fna/tax.ts)."""

    schemaVersion: int = 1
    taxYear: str
    startDate: str
    donations: Donations
    savingsWithdrawal: SavingsWithdrawal
    livingAnnuity: LivingAnnuity


# --------------------------------------------------------------------------- loading


def _file_name(tax_year: str) -> str:
    return f"za_{tax_year.replace('/', '_')}.json"


def _last_of_february(year: int) -> date:
    return date(year, 3, 1) - timedelta(days=1)


@lru_cache(maxsize=4)
def _validator(schema_path: str) -> Draft202012Validator:
    schema = json.loads(Path(schema_path).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _bracket_tax(amount: float, brackets: list[dict]) -> float:
    if amount <= 0:
        return 0.0
    b = brackets[0]
    for x in brackets:
        if amount > x["from"]:
            b = x
    return b["base"] + (amount - b["from"]) * b["rate"]


def _check(raw: dict, name: str) -> list[str]:
    """Consistency checks a JSON Schema cannot express."""
    errors: list[str] = []
    year = raw["taxYear"]
    if name != _file_name(year):
        errors.append(f"file name should be {_file_name(year)} for tax year {year}")
    start = int(year[:4])
    if int(year[5:]) != (start + 1) % 100:
        errors.append(f"taxYear {year} does not span two consecutive years")
    if date.fromisoformat(raw["startDate"]) != date(start, 3, 1):
        errors.append(f"startDate should be {date(start, 3, 1)}")
    if date.fromisoformat(raw["endDate"]) != _last_of_february(start + 1):
        errors.append(f"endDate should be {_last_of_february(start + 1)}")
    for key in ("brackets", "retirementLumpSum", "withdrawalLumpSum"):
        rows = raw[key]
        for prev, cur in pairwise(rows):
            if cur["from"] <= prev["from"]:
                errors.append(f"{key}: bracket from {cur['from']} is not above {prev['from']}")
            elif abs(prev["base"] + (cur["from"] - prev["from"]) * prev["rate"] - cur["base"]) > 1:
                errors.append(f"{key}: base {cur['base']} at {cur['from']} does not reconcile with the bracket below")
    r = raw["rebates"]
    for age, (label, rebate) in {
        40: ("under65", r["primary"]),
        70: ("age65to74", r["primary"] + r["secondary"]),
        80: ("age75plus", r["primary"] + r["secondary"] + r["tertiary"]),
    }.items():
        threshold = raw["thresholds"][label]
        if _bracket_tax(threshold - 100, raw["brackets"]) > rebate or _bracket_tax(threshold + 100, raw["brackets"]) <= rebate:
            errors.append(f"thresholds.{label} {threshold} is not where tax first exceeds the rebates at age {age}")
    la = raw["livingAnnuity"]
    if la["minDrawdown"] >= la["maxDrawdown"]:
        errors.append("livingAnnuity.minDrawdown must be below maxDrawdown")
    return errors


def load_table_file(path: Path, schema_path: Path | None = None) -> TaxTableFile:
    """Read one table file, validate it against ``schema.json`` and the consistency checks."""
    raw = json.loads(path.read_text())
    validator = _validator(str(schema_path or path.parent / "schema.json"))
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in validator.iter_errors(raw)]
    if not errors:
        errors = _check(raw, path.name)
    if errors:
        raise TableError(f"{path}: " + "; ".join(errors))
    raw.pop("$schema", None)
    return TaxTableFile.model_validate(raw)


@lru_cache(maxsize=8)
def _load_dir(tables_dir: str) -> dict[str, TaxTableFile]:
    d = Path(tables_dir)
    files = sorted(d.glob("za_[0-9][0-9][0-9][0-9]_[0-9][0-9].json"), reverse=True)  # newest first, like TAX_YEARS()
    if not files:
        raise TableError(f"no za_<yyyy>_<yy>.json table files in {d}")
    tables = {}
    for f in files:
        t = load_table_file(f, d / "schema.json")
        tables[t.taxYear] = t
    return tables


def _tables(tables_dir: Path | str | None = None) -> dict[str, TaxTableFile]:
    return _load_dir(str(tables_dir or get_settings().fna_tables_dir))


def available_tax_years(tables_dir: Path | str | None = None) -> list[str]:
    """Tax years with a table file, newest first."""
    return list(_tables(tables_dir))


def latest_tax_year(tables_dir: Path | str | None = None) -> str:
    return available_tax_years(tables_dir)[0]


def get_table(tax_year: str = DEFAULT_TAX_YEAR, *, tables_dir: Path | str | None = None) -> TaxTableFile:
    """The table file for ``tax_year``; raises ``KeyError`` naming the available years if there is none."""
    tables = _tables(tables_dir)
    try:
        return tables[tax_year]
    except KeyError:
        raise KeyError(f"no SARS table for {tax_year!r}; available: {', '.join(tables)}") from None


def table_for(tax_year: str, *, tables_dir: Path | str | None = None) -> TaxTableFile:
    """``getTaxTable``: the year's table, or the default year's for an unknown year (what the app does)."""
    tables = _tables(tables_dir)
    t = tables.get(tax_year)
    return t if t is not None else tables[DEFAULT_TAX_YEAR]


# --------------------------------------------------------------------------- projection beyond the latest year


def _scale(brackets: tuple[Bracket, ...], f: float) -> tuple[Bracket, ...]:
    return tuple(Bracket(from_=b.from_ * f, base=b.base * f, rate=b.rate) for b in brackets)


def _shift_year(tax_year: str, years: int) -> str:
    start = int(tax_year[:4]) + years
    return f"{start}/{(start + 1) % 100:02d}"


def indexed_table(table: TaxTableFile, years_ahead: int, cpi: float, *, index_limits: bool = False) -> TaxTableFile:
    """Project ``table`` ``years_ahead`` years forward with CPI (§12.3).

    Brackets (bounds and base amounts), rebates, thresholds and the s6A medical credits grow by
    (1 + cpi) ** years_ahead, as SARS adjusts them each Budget. Other rand limits (the s11F cap, CGT
    exclusions, TFSA limits, UIF ceiling, interest exemption, estate duty and donations amounts and the
    lump-sum tables) change only when legislated, so they keep their nominal values unless ``index_limits``.
    Amounts are not rounded, so tax on an indexed table equals the factor times tax on the base table at
    income / factor (``tax.income_tax_vec(..., index=factor)`` uses this to index per simulation path).
    """
    if years_ahead < 0 or int(years_ahead) != years_ahead:
        raise ValueError("years_ahead must be a whole number of years ≥ 0")
    if years_ahead == 0:
        return table
    f = (1 + cpi) ** years_ahead
    year = _shift_year(table.taxYear, int(years_ahead))
    start = int(year[:4])
    end = _last_of_february(start + 1)
    update: dict = {
        "taxYear": year,
        "label": f"{year} (indexed)",
        "period": f"1 March {start} – {end.day} February {start + 1}",
        "startDate": date(start, 3, 1).isoformat(),
        "endDate": end.isoformat(),
        "status": "provisional",
        "brackets": _scale(table.brackets, f),
        "rebates": Rebates(**{k: v * f for k, v in table.rebates}),
        "thresholds": Thresholds(**{k: v * f for k, v in table.thresholds}),
        "medicalCredits": MedicalCredits(**{k: v * f for k, v in table.medicalCredits}),
        "sources": (*table.sources, f"Projected from {table.taxYear} with CPI of {cpi:.2%} a year for {years_ahead} years"),
    }
    if index_limits:
        update |= {
            "retirementDeduction": table.retirementDeduction.model_copy(update={"cap": table.retirementDeduction.cap * f}),
            "cgt": table.cgt.model_copy(
                update={k: getattr(table.cgt, k) * f for k in ("annualExclusion", "deathExclusion", "primaryResidence")}
            ),
            "tfsa": Tfsa(annual=table.tfsa.annual * f, lifetime=table.tfsa.lifetime * f),
            "uif": table.uif.model_copy(update={"ceilingMonthly": table.uif.ceilingMonthly * f}),
            "interestExemption": InterestExemption(under65=table.interestExemption.under65 * f, over65=table.interestExemption.over65 * f),
            "estateDuty": table.estateDuty.model_copy(
                update={"abatement": table.estateDuty.abatement * f, "threshold": table.estateDuty.threshold * f}
            ),
            "retirementLumpSum": _scale(table.retirementLumpSum, f),
            "withdrawalLumpSum": _scale(table.withdrawalLumpSum, f),
            "donations": table.donations.model_copy(
                update={"annualExemption": table.donations.annualExemption * f, "threshold": table.donations.threshold * f}
            ),
            "savingsWithdrawal": table.savingsWithdrawal.model_copy(update={"minimum": table.savingsWithdrawal.minimum * f}),
        }
    data = {name: getattr(table, name) for name in TaxTableFile.model_fields} | update
    return TaxTableFile.model_construct(**data)


def table_for_year(start_year: int, cpi: float, *, index_limits: bool = False, tables_dir: Path | str | None = None) -> TaxTableFile:
    """The table for the tax year starting 1 March ``start_year``: its file if there is one, otherwise the
    latest file indexed by CPI for the years after it (§12.3)."""
    tables = _tables(tables_dir)
    year = f"{start_year}/{(start_year + 1) % 100:02d}"
    if year in tables:
        return tables[year]
    latest = next(iter(tables.values()))
    ahead = start_year - int(latest.taxYear[:4])
    if ahead < 0:
        raise KeyError(f"no SARS table for {year}; the earliest is {list(tables)[-1]}")
    return indexed_table(latest, ahead, cpi, index_limits=index_limits)
