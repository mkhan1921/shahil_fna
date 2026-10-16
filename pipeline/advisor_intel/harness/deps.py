"""What the orchestrator's tools work with (spec §13.2–13.3).

``AgentInputs`` is what the ``run_agent`` activity loads from storage; ``HarnessServices`` are the
work behind the tools that live in other stages (verification, constraints, sensitivity, follow-ups,
term lookup), injected so the harness runs with whatever is configured; ``HarnessDeps`` is the run's
working state. Values only change through ``HarnessDeps.apply``, which runs the reducer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from ..config import Budgets
from ..extract.reducer import ReducerResult, apply_state_ops
from ..models.crm import CrmRecord
from ..models.entities import DiscourseOutput, Entity
from ..models.extraction import ExtractionResult, ExtractionTask
from ..models.fna import FnaDocument
from ..models.profile import Profile, StateOp
from ..models.runs import VersionPins
from ..models.transcript import QualityReport, Transcript
from ..schema.slots import Catalog
from .budget import BudgetGuard
from .loop import LoopDetector
from .state import HarnessState


class AgentInputs(BaseModel):
    """Everything ``run_agent`` needs, loaded by the activity from storage (§13.1–13.2)."""

    call_id: str
    run_id: str
    transcript: Transcript
    discourse: DiscourseOutput
    profile: Profile  # seeded by load_prior; the discourse registry is merged in by run_agent
    pins: VersionPins = Field(default_factory=VersionPins)
    budgets: Budgets = Field(default_factory=Budgets)
    quality: QualityReport | None = None
    crm: CrmRecord | None = None
    glossary: list[str] = []  # confirmed practice terms (§8.5)
    base_document: FnaDocument | None = None  # the client's FNA before the call
    call_minutes: float | None = None


@dataclass
class ServiceResult:
    """What a work service returns: an updated profile (or None), data for the agent, usage."""

    profile: Profile | None = None
    data: Any = None
    usage: dict[str, float] = field(default_factory=dict)


ExtractFn = Callable[[ExtractionTask, "HarnessDeps"], Awaitable[tuple[Profile, ExtractionResult]]]
ProfileFn = Callable[[Profile, "HarnessDeps"], Awaitable[ServiceResult]]
VerifyFn = Callable[[Profile, list[str], "HarnessDeps"], Awaitable[ServiceResult]]
LookupFn = Callable[[str, str, "HarnessDeps"], Awaitable[ServiceResult]]


@dataclass
class HarnessServices:
    extract: ExtractFn
    verify: VerifyFn | None = None
    constraints: ProfileFn | None = None
    sensitivity: ProfileFn | None = None
    followups: ProfileFn | None = None
    lookup_term: LookupFn | None = None
    derive: Callable[[Profile, HarnessDeps], Awaitable[Profile]] | None = None


ProfileSink = Callable[[Profile], Awaitable[None]]


@dataclass
class HarnessDeps:
    inputs: AgentInputs
    catalog: Catalog
    services: HarnessServices
    state: HarnessState
    budget: BudgetGuard
    loop: LoopDetector
    profile: Profile
    on_profile: ProfileSink | None = None
    context_window_tokens: int = 200_000

    @property
    def pins(self) -> VersionPins:
        return self.inputs.pins

    @property
    def transcript(self) -> Transcript:
        return self.inputs.transcript

    @property
    def discourse(self) -> DiscourseOutput:
        return self.inputs.discourse

    @property
    def entities(self) -> dict[str, Entity]:
        return self.profile.entities

    async def set_profile(self, profile: Profile) -> None:
        self.profile = profile
        self.state.profile_version = profile.version
        if self.on_profile is not None:
            await self.on_profile(profile)

    async def apply(self, ops: list[StateOp]) -> ReducerResult:
        """The only way the harness changes values: the reducer (§7.5)."""
        result = apply_state_ops(self.profile, ops)
        await self.set_profile(result.profile)
        return result

    def add_usage(self, usage: dict[str, Any] | None, *, role: str | None = None) -> None:
        """Count a sub-agent's usage. One that reports tokens but no ``cost_usd`` is priced from the model
        the given role runs on, so the USD cap covers every model (§13.6)."""
        if not usage:
            return
        if role is not None and not usage.get("cost_usd") and (usage.get("input_tokens") or usage.get("output_tokens")):
            from .. import llm

            usage = {
                **usage,
                "cost_usd": llm.usage_cost_usd(
                    llm.role_model_name(role, self.pins),  # type: ignore[arg-type]
                    input_tokens=int(usage.get("input_tokens", 0) or 0),
                    output_tokens=int(usage.get("output_tokens", 0) or 0),
                    cache_read_tokens=int(usage.get("cache_read_tokens", 0) or 0),
                    cache_write_tokens=int(usage.get("cache_write_tokens", 0) or 0),
                ),
            }
        self.state.subagent_usage.add(usage)

    def brief(self) -> str:
        """The initial brief (§13.2): metadata, quality, registry summary, index counts, tier A slots and
        budgets — never the transcript, which the agent reads through tools."""
        t = self.transcript
        q = self.inputs.quality or t.quality
        lines = [
            f"# Call {self.inputs.call_id} (run {self.inputs.run_id})",
            f"Call date {t.anchor_date.isoformat()}, {len(t.segments)} segments, {t.duration / 60:.0f} minutes, ASR {t.asr_provider} {t.asr_model_version}.",
        ]
        if q:
            lines.append(f"Quality: mean word confidence {q.mean_word_conf:.2f}, low-ASR share {q.low_asr_share:.0%}, crosstalk {q.crosstalk_share:.0%}, "
                         f"{q.n_speakers} speakers, minimum role confidence {q.role_conf_min:.2f}." + (f" Banners: {'; '.join(q.banners)}" if q.banners else ""))
        ents = self.entities
        persons = [e for e in ents.values() if e.type == "person"]
        lines.append(f"\n## Registry\n{len(ents)} entities; people: " + ", ".join(f"{e.entity_id} ({e.canonical_name})" for e in persons[:12]))
        priors = sum(1 for f in self.profile.facts.values() if f.source == "prior_fna")
        lines.append(f"Prior FNA: {priors} fields from the client's existing document." if priors else "No prior FNA document: every field is new.")
        lines.append("\n## Topic index (segments per slot group)")
        for g in self.catalog.groups:
            lines.append(f"- {g}: {len(self.discourse.topic_index.get(g, []))}")
        tier_a = sorted({s.slot_id for s in self.catalog.tier_a()})
        lines.append("\n## Tier A slots\n" + ", ".join(tier_a))
        b = self.inputs.budgets
        lines.append(f"\n## Budget\n{b.orchestrator_requests} requests, {b.tool_calls} tool calls, {b.total_tokens:,} tokens, ${b.usd_cap:.0f}, "
                     f"{b.wall_clock_soft_s // 60} minutes; at most {b.reextractions_per_group} re-extractions per slot group.")
        lines.append("\nStart with make_plan.")
        return "\n".join(lines)
