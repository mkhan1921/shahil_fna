"""Contracts inside Stage 1 (§5): what ASR providers and diarizers return, the biasing vocabulary and call metadata."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

PhraseKind = Literal["vocab", "name", "product", "amount", "age", "distractor"]
# Order in which phrases are offered to a provider, and the share of a limited keyterm budget each kind is
# guaranteed before leftovers are filled in priority order (§5.3, §5.4).
PHRASE_PRIORITY: dict[str, int] = {"name": 0, "product": 1, "amount": 2, "age": 3, "vocab": 4, "distractor": 5}
PHRASE_QUOTAS: dict[str, float] = {"name": 0.20, "product": 0.15, "amount": 0.25, "age": 0.05, "vocab": 0.25, "distractor": 0.10}


class AsrWord(BaseModel):
    """One word as a provider returns it, before diarization and segmentation."""

    text: str  # the 1-best word as displayed (the provider's ITN form when it applied ITN)
    t_start: float  # seconds, in the result's time base
    t_end: float
    conf: float
    alts: list[tuple[str, float]] = []  # per-word alternatives (or aligned n-best) with scores in (0, 1]
    raw_spoken: str | None = None  # the spoken form when the provider's ITN changed it ("two fifty" for "R250")
    channel: int | None = None  # 0-based audio channel for multichannel requests
    speaker: str | None = None  # the provider's own speaker label, when it diarized


class AsrResult(BaseModel):
    words: list[AsrWord]
    provider: str
    model_version: str
    alternatives_available: bool  # False when the provider gave no n-best and no word alternatives (§5.2 step 3)
    language: str | None = None
    time_base: Literal["input", "call"] = "input"  # "input": relative to the audio sent; "call": already call-relative
    duration: float | None = None  # seconds of audio the provider processed


class BiasPhrase(BaseModel):
    """One phrase for keyterm prompting or custom vocabulary (§5.3 per-call biasing, §5.4)."""

    text: str
    boost: float = 1.0
    kind: PhraseKind = "vocab"
    sounds_like: list[str] = []
    source: str = ""  # FnaDocument path or vocabulary file it came from


class Vocabulary(BaseModel):
    """The phrase list handed to an ASR provider: the static SA finance vocabulary plus per-call biasing."""

    phrases: list[BiasPhrase] = []

    def ordered(self, kinds: set[PhraseKind] | None = None) -> list[BiasPhrase]:
        """Unique phrases (first occurrence wins), highest priority first, then by boost."""
        seen: set[str] = set()
        out = []
        for p in sorted(self.phrases, key=lambda p: (PHRASE_PRIORITY[p.kind], -p.boost)):
            key = p.text.strip().lower()
            if not key or key in seen or (kinds is not None and p.kind not in kinds):
                continue
            seen.add(key)
            out.append(p)
        return out

    def select(self, limit: int | None = None, *, max_words: int | None = None, kinds: set[PhraseKind] | None = None) -> list[BiasPhrase]:
        """Phrases for a provider with a keyterm limit: each kind gets its quota, leftovers go by priority."""
        pool = [p for p in self.ordered(kinds) if max_words is None or len(p.text.split()) <= max_words]
        if limit is None or len(pool) <= limit:
            return pool
        chosen: list[BiasPhrase] = []
        for kind, share in PHRASE_QUOTAS.items():
            chosen += [p for p in pool if p.kind == kind][: int(share * limit)]
        picked = {id(p) for p in chosen}
        chosen += [p for p in pool if id(p) not in picked][: limit - len(chosen)]
        return sorted(chosen, key=lambda p: (PHRASE_PRIORITY[p.kind], -p.boost))

    def terms(self, limit: int | None = None, *, max_words: int | None = None, kinds: set[PhraseKind] | None = None) -> list[str]:
        """Phrase texts for a provider, capped in count and in words per phrase."""
        return [p.text for p in self.select(limit, max_words=max_words, kinds=kinds)]

    def merged(self, other: Vocabulary | None) -> Vocabulary:
        return Vocabulary(phrases=[*self.phrases, *(other.phrases if other else [])])

    def __len__(self) -> int:
        return len(self.phrases)


class SpeakerTurn(BaseModel):
    """One diarization turn (§5.2 step 4)."""

    speaker: str
    start: float
    end: float
    confidence: float = 1.0  # mean posterior of the assigned speaker (1.0 for channel-based)
    channel: int | None = None


class CallMeta(BaseModel):
    """Call metadata for ingestion (§5.1)."""

    call_id: str
    started_at: datetime
    advisor_id: str = ""
    client_ids: list[str] = []
    language: str = "en-ZA"
    advisor_channel: int | None = None  # stereo: the channel the recorder puts the adviser on, when known
    shared_client_channel: bool | None = None  # two clients on one channel (spouse on speaker-phone); default: ≥ 2 client ids
    anchor_date: date | None = Field(default=None, description="call date for relative time; defaults to started_at's date")

    @property
    def anchor(self) -> date:
        return self.anchor_date or self.started_at.date()

    def clients_share_a_channel(self) -> bool:
        return self.shared_client_channel if self.shared_client_channel is not None else len(self.client_ids) >= 2


class CrmNames(BaseModel):
    """Names from the CRM used as role hints (§5.2 step 6) and vocabulary (§5.4)."""

    advisor: list[str] = []  # the adviser's names ("Sipho Dlamini", "Sipho")
    client_primary: list[str] = []
    client_secondary: list[str] = []
    others: list[str] = []  # dependants, businesses, properties: vocabulary only

    def all_names(self) -> list[str]:
        return [*self.advisor, *self.client_primary, *self.client_secondary, *self.others]


class AsrError(RuntimeError):
    """A provider rejected or failed a request (retried by the Temporal activity, §13.1)."""
