"""Entities and mentions (spec §3.2, §6.1–6.2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

EntityType = Literal["person", "account", "policy", "property", "business", "liability", "goal", "institution"]

# The slot catalog names list items by their FnaDocument list; this maps them to entity types.
FNA_LIST_ENTITY: dict[str, EntityType] = {
    "dependant": "person",
    "fund": "account",
    "asset": "property",
    "liability": "liability",
    "policy": "policy",
    "goal": "goal",
    "expense": "account",
    "event": "goal",
}


class Entity(BaseModel):
    entity_id: str  # "person.daughter_1", "policy.discovery_life_1"
    type: EntityType
    canonical_name: str
    aliases: list[str] = []  # surface forms seen: "my girl", "Thandi", "she"
    attributes: dict[str, Any] = {}  # e.g. {"relation": "daughter"}
    crm_match: list[tuple[str, float]] = []  # top-k CRM ids with scores
    first_mention: str | None = None  # segment_id; None for seeded entities not yet mentioned
    fna_list: str | None = None  # "dependants", "policies", … when the entity is an FnaDocument list row
    fna_id: str | None = None  # the row's id in the FnaDocument, so facts resolve to dependants[<id>]

    def fna_ref(self) -> str | None:
        return f"{self.fna_list}[{self.fna_id}]" if self.fna_list and self.fna_id else None


class Mention(BaseModel):
    segment_id: str
    char_start: int
    char_end: int
    surface: str
    entity_id: str | None
    resolution_conf: float


class Revision(BaseModel):
    """A speaker revising an earlier value (§6.4)."""

    segment_id: str
    revises_segment_id: str
    scope: str  # slot group guess
    trigger: str = ""  # "actually", "no wait", repeated number, spouse correction …


class DiscourseOutput(BaseModel):
    """Everything Stage 2 adds to a transcript, cached per (call, versions)."""

    entities: dict[str, Entity]
    mentions: list[Mention] = []
    ambiguous_refs: list[str] = []  # segment ids with an unresolved low-confidence mention
    rewrites: dict[str, str] = {}  # segment id → self-contained rewrite that passed the checks
    rewrite_sources: dict[str, list[str]] = {}  # segment id → earlier segments whose numbers the rewrite used
    revisions: list[Revision] = []
    topic_index: dict[str, list[str]] = {}  # slot group → segment ids (relevance ≥ 0.3)
    topic_scores: dict[str, dict[str, float]] = {}  # segment id → {slot group: relevance}
    possible_duplicates: list[tuple[str, str]] = []
    embedding_model: str | None = None
    segment_vectors: dict[str, list[float]] = {}  # reused by search_transcript in semantic mode (§13.3)
    rewrite_rejections: dict[str, str] = {}  # segment id → the check a rewrite failed (§6.3)
    usage: dict[str, int] = {}  # model requests and tokens spent in this stage
