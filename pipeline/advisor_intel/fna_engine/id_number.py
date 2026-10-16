"""South African ID numbers (YYMMDD SSSS C A Z) and ages: a port of ``lib/fna/idNumber.ts`` (§12.7, §7.6).

- YYMMDD: date of birth; the century is the latest one that does not put the birth in the future year
- SSSS: 0000–4999 female, 5000–9999 male
- C: 0 = SA citizen, 1 = permanent resident, 2 = refugee
- A: historical race digit, now typically 8 or 9
- Z: Luhn check digit
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal, NamedTuple

from pydantic import BaseModel, Field

from ._js import Now, date_ms, parse_local_date, strip_js_space

Citizenship = Literal["SA citizen", "Permanent resident", "Refugee"]
_THIRTEEN_DIGITS = re.compile(r"[0-9]{13}", re.ASCII)
_MS_PER_YEAR = 365.2425 * 24 * 3600 * 1000


class ParsedId(BaseModel):
    """``ParsedId``: ``error`` is set when the number is not valid."""

    valid: bool
    error: str | None = Field(default=None, exclude_if=lambda v: v is None)
    dateOfBirth: str | None = Field(default=None, exclude_if=lambda v: v is None)
    gender: Literal["male", "female"] | None = Field(default=None, exclude_if=lambda v: v is None)
    citizenship: Citizenship | None = Field(default=None, exclude_if=lambda v: v is None)


class IdDetails(NamedTuple):
    date_of_birth: date
    gender: Literal["male", "female"]
    citizen: bool  # SA citizen (permanent residents and refugees are not)


def luhn_valid(digits: str) -> bool:
    total = 0
    double = False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if double:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        double = not double
    return total % 10 == 0


def parse_sa_id(raw: str, today: date | datetime | None = None) -> ParsedId:
    """``parseSaId``: validate an ID number and read the date of birth, gender and citizenship."""
    sa_id = strip_js_space(raw or "")
    if not sa_id:
        return ParsedId(valid=False, error="Empty")
    if not _THIRTEEN_DIGITS.fullmatch(sa_id):
        return ParsedId(valid=False, error="An SA ID number has 13 digits")
    yy, mm, dd = int(sa_id[0:2]), int(sa_id[2:4]), int(sa_id[4:6])
    current_yy = Now.of(today).year % 100
    year = 2000 + yy if yy <= current_yy else 1900 + yy
    try:
        dob = date(year, mm, dd)
    except ValueError:
        return ParsedId(valid=False, error="Date of birth digits are not a real date")
    citizen_digit = int(sa_id[10])
    if citizen_digit > 2:
        return ParsedId(valid=False, error="Citizenship digit must be 0, 1 or 2")
    if not luhn_valid(sa_id):
        return ParsedId(valid=False, error="Check digit does not match (possible typo)")
    return ParsedId(
        valid=True,
        dateOfBirth=dob.isoformat(),
        gender="male" if int(sa_id[6:10]) >= 5000 else "female",
        citizenship=("SA citizen", "Permanent resident", "Refugee")[citizen_digit],
    )


def parse_id(raw: str, today: date | datetime | None = None) -> IdDetails | None:
    """Date of birth, gender and citizenship from an ID number, or None when it fails validation (§7.6)."""
    p = parse_sa_id(raw, today)
    if not p.valid:
        return None
    return IdDetails(date.fromisoformat(p.dateOfBirth), p.gender, p.citizenship == "SA citizen")  # type: ignore[arg-type]


def _age_on(dob: date, year: int, month: int, day: int) -> int:
    age = year - dob.year
    m = month - dob.month
    if m < 0 or (m == 0 and day < dob.day):
        age -= 1
    return age


def age_on(date_of_birth: str, at: date | datetime | Now | None = None) -> int | None:
    """``ageOn``: age in completed years on ``at``; None without a valid date of birth."""
    dob = parse_local_date(date_of_birth)
    if dob is None:
        return None
    now = Now.of(at)
    return _age_on(dob, now.year, now.month, now.day)


def age_next_birthday(date_of_birth: str, at: date | datetime | Now | None = None) -> int | None:
    """Age next birthday, the basis most SA life insurers price on."""
    age = age_on(date_of_birth, at)
    return None if age is None else age + 1


def exact_age(date_of_birth: str, at: date | datetime | Now | None = None) -> float | None:
    """``exactAge``: fractional years from midnight on the birth date to the instant ``at``."""
    dob = parse_local_date(date_of_birth)
    if dob is None:
        return None
    return (Now.of(at).ms - date_ms(dob)) / _MS_PER_YEAR


__all__ = ["IdDetails", "ParsedId", "age_next_birthday", "age_on", "exact_age", "luhn_valid", "parse_id", "parse_sa_id"]
