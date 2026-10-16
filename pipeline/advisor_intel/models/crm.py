"""The practice's CRM record for a client household (spec §3.6, §6.1, §7.2 context pack).

Only what the pipeline reads: household members, products held and the adviser's notes. The CRM
seeds the entity registry (``crm_match``), biases ASR toward known names and figures (§5.3), and
goes into every extraction context pack.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CrmPerson(BaseModel):
    crm_id: str
    role: Literal["client_primary", "spouse", "child", "parent", "sibling", "other", "advisor"]
    name: str
    date_of_birth: str = ""  # ISO yyyy-mm-dd when known
    id_number: str = ""


class CrmProduct(BaseModel):
    crm_id: str
    kind: Literal["policy", "fund", "investment", "loan", "bank", "property", "other"]
    provider: str = ""
    product: str = ""  # product or plan name
    number: str = ""  # policy or account number
    owner: Literal["client", "spouse", "joint", ""] = ""
    fna_type: str = ""  # the FnaDocument type when known: 'retirement-annuity', 'life', 'home-loan'…
    last_value: float | None = None  # last known value, cover or balance
    as_of: str = ""  # ISO date of last_value


class CrmRecord(BaseModel):
    client_id: str
    household: list[CrmPerson] = []
    products: list[CrmProduct] = []
    adviser_name: str = ""
    adviser_notes: str = ""
