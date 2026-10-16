"""The profile schema: slot catalog (``profile_v1.yaml``), derivations, constraints and the strict-schema compiler."""

from .slots import (
    CURRENT_VERSION,
    LIST_OF_ENTITY,
    PERSONS,
    Catalog,
    SlotDef,
    SlotGroup,
    fact_id_for,
    generic_slot_id,
    load_catalog,
    person_of,
)

__all__ = [
    "CURRENT_VERSION",
    "LIST_OF_ENTITY",
    "PERSONS",
    "Catalog",
    "SlotDef",
    "SlotGroup",
    "fact_id_for",
    "generic_slot_id",
    "load_catalog",
    "person_of",
]
