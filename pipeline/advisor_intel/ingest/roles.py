"""Speaker-role assignment (§5.2 step 6): ``speaker_label → speaker_role`` with a confidence per label.

A frontier-model Pydantic AI agent reads the first five minutes plus a sample of later turns, with the
CRM names of the adviser and clients as hints. A deterministic heuristic scores each speaker on the same
signals (greeting and agenda setting, questions asked, profile questions, names addressed, self
introductions, first-person money talk). It goes into the prompt as a feature, and it is the answer when
the run's model is a test model or the agent's output fails validation. Where the agent and a confident
heuristic disagree, the confidence is held below 0.8, so the reviewer confirms that speaker (one click).
Any role confidence below 0.8 sets ``role_review_needed`` (§5.5).

The agent's output model is strict-schema friendly (every field required, no dict-valued fields) and is
converted to the shared ``RoleAssignment``.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior

from .. import llm
from ..models.runs import VersionPins
from ..models.transcript import RoleAssignment, Segment, SpeakerRole
from .types import CrmNames

log = logging.getLogger(__name__)

FIRST_WINDOW_S = 300.0
LATER_SAMPLE = 30
MAX_PROMPT_CHARS = 14_000
MIN_ROLE_CONF = 0.8
UNIQUE_ROLES: tuple[SpeakerRole, ...] = ("advisor", "client_primary", "client_secondary")


class SpeakerRoleItem(BaseModel):
    speaker_label: str = Field(description="The diarization label exactly as given, e.g. 'spk_0' or 'ch1'.")
    role: SpeakerRole = Field(description="advisor, client_primary, client_secondary, other or unknown.")
    confidence: float = Field(description="Probability 0..1 that the role is right. Above 0.95 only when a name or an explicit statement decides it.")
    evidence: str = Field(description="One line: the segment ids, names or behaviour that decided it.")


class RoleAssignmentOutput(BaseModel):
    speakers: list[SpeakerRoleItem] = Field(description="One entry for every speaker label in the transcript.")
    rationale: str = Field(description="Two or three sentences on how the roles were decided.")


ROLE_INSTRUCTIONS = """\
You assign speaker roles in a recorded meeting between a South African financial adviser and their clients.

Roles:
- advisor: the financial adviser who runs the meeting. They greet, set the agenda, make compliance
  statements, ask the profile questions (income, expenses, retirement, policies, dependants) and explain products.
- client_primary: the main client (the CRM's primary client when names are given).
- client_secondary: a second client, usually the spouse or partner of the primary client.
- other: anyone else who speaks (a child, an assistant, an interpreter, a colleague of the adviser).
- unknown: you cannot tell.

Rules:
- Return every speaker label you are given, exactly as written, once.
- At most one advisor, one client_primary and one client_secondary.
- Use the CRM names: a speaker who introduces themselves by a name, or is addressed by it in the next or
  previous turn ("thanks Sipho"), is that person.
- The heuristic guess is a computed feature. Use it, but overrule it when the transcript says otherwise.
- Confidence is a probability. Keep it below 0.8 when the evidence is thin, so a person confirms the role.
"""

role_agent: Agent[None, RoleAssignmentOutput] = Agent(
    name="role_assignment",
    output_type=NativeOutput(RoleAssignmentOutput),
    instructions=ROLE_INSTRUCTIONS,
    retries=2,
)

_GREETING = re.compile(
    r"\b(good (morning|afternoon|evening)|thanks? (you )?(so much )?for (making|taking|finding) (the )?time|thank you for coming|welcome|"
    r"today (we('ll| will)|we are going to|i('d| would) like)|the (plan|agenda) (for|today)|let's (start|begin|get started|kick off)|"
    r"before we (start|begin)|(this|the) (call|meeting) (is|will be) (being )?recorded|i('ll| will) be recording|as your (financial )?"
    r"(adviser|advisor|planner)|fais|disclosure|popia|consent to|i('m| am) (an?|your) (financial )?(adviser|advisor|planner)|"
    r"compliance|record of advice)\b"
)
_QUESTION_START = re.compile(
    r"^(so |and |okay |ok |right |now )?(what|how|when|where|who|which|do you|did you|are you|have you|has |is there|is it|could you|"
    r"can you|would you|tell me|any )\b"
)
_PROFILE = re.compile(
    r"\b(income|salary|earn|take home|retire|retirement|policy|policies|cover|children|kids|medical aid|budget|expenses|spend|"
    r"savings|debt|bond|will|beneficiar|born|age|married|employer|work|contribut|provident|pension|annuity|tfsa|invest|fund)"
)
_FIRST_PERSON_MONEY = re.compile(
    r"\b(i|we) (earn|make|take home|pay|spend|owe|have|got|put|contribute|save|bought|want to retire|retire)\b|"
    r"\bmy (salary|wife|husband|kids|children|son|daughter|bond|house|car|policy|pension|ra|employer|boss|mother|mom|mum|dad|job)\b|"
    r"\bour (house|bond|kids|children|budget)\b"
)
_SELF_INTRO = re.compile(r"\b(my name is|this is|i'm|i am|it's|dis) ([a-zà-ÿ'-]+)")


@dataclass
class SpeakerFeatures:
    label: str
    speech_s: float = 0.0
    n_segments: int = 0
    first_t: float = math.inf
    questions: int = 0
    profile_questions: int = 0
    greeting_agenda: int = 0
    first_person_money: int = 0
    addressed_as: dict[str, int] = field(default_factory=dict)  # crm role → times the neighbouring speaker used that name
    says_names: dict[str, int] = field(default_factory=dict)  # crm role → times this speaker said that name
    introduced_as: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        names = ", ".join(f"addressed as {k}×{v}" for k, v in self.addressed_as.items())
        intro = ", ".join(f"introduces self as {k}" for k in self.introduced_as)
        said = ", ".join(f"says {k}'s name ×{v}" for k, v in self.says_names.items())
        bits = [f"{self.speech_s:.0f}s of speech in {self.n_segments} turns", f"first speaks at {self.first_t:.0f}s",
                f"{self.questions} questions ({self.profile_questions} about the client's finances)", f"{self.greeting_agenda} greeting/agenda/compliance lines",
                f"{self.first_person_money} first-person money statements"]
        return "; ".join(bits + [x for x in (names, intro, said) if x])


def _name_patterns(crm: CrmNames | None) -> dict[str, list[re.Pattern]]:
    if crm is None:
        return {}
    out: dict[str, list[re.Pattern]] = {}
    for role, names in (("advisor", crm.advisor), ("client_primary", crm.client_primary), ("client_secondary", crm.client_secondary)):
        forms: set[str] = set()
        for n in names:
            n = n.strip().lower()
            if not n:
                continue
            forms.add(n)
            forms.add(n.split()[0])
        out[role] = [re.compile(rf"\b{re.escape(f)}\b") for f in sorted(forms, key=len, reverse=True) if len(f) >= 2]
    return out


def speaker_features(segments: Sequence[Segment], crm: CrmNames | None = None) -> dict[str, SpeakerFeatures]:
    feats: dict[str, SpeakerFeatures] = {}
    patterns = _name_patterns(crm)
    ordered = sorted(segments, key=lambda s: s.t_start)
    for i, s in enumerate(ordered):
        f = feats.setdefault(s.speaker_label, SpeakerFeatures(label=s.speaker_label))
        text = s.text.lower()
        f.speech_s += max(0.0, s.t_end - s.t_start)
        f.n_segments += 1
        f.first_t = min(f.first_t, s.t_start)
        for sentence in re.split(r"(?<=[.?!])\s+", text):
            q = sentence.strip().endswith("?") or bool(_QUESTION_START.match(sentence.strip()))
            if q:
                f.questions += 1
                if _PROFILE.search(sentence):
                    f.profile_questions += 1
        f.greeting_agenda += len(_GREETING.findall(text))
        f.first_person_money += len(_FIRST_PERSON_MONEY.findall(text))
        intros = [m.group(2) for m in _SELF_INTRO.finditer(text)]
        for role, pats in patterns.items():
            if any(p.search(text) for p in pats):
                if any(any(p.fullmatch(name) or p.match(name) for p in pats) for name in intros):
                    f.introduced_as[role] = f.introduced_as.get(role, 0) + 1
                    continue
                f.says_names[role] = f.says_names.get(role, 0) + 1
                neighbour = next((o for o in (ordered[i - 1] if i else None, ordered[i + 1] if i + 1 < len(ordered) else None)
                                  if o is not None and o.speaker_label != s.speaker_label), None)
                if neighbour is not None:
                    nf = feats.setdefault(neighbour.speaker_label, SpeakerFeatures(label=neighbour.speaker_label))
                    nf.addressed_as[role] = nf.addressed_as.get(role, 0) + 1
    return feats


def _identity(f: SpeakerFeatures, role: str) -> float:
    return 3.0 * f.introduced_as.get(role, 0) + 1.0 * f.addressed_as.get(role, 0)


def _advisor_score(f: SpeakerFeatures, earliest: float) -> float:
    n = max(1, f.n_segments)
    score = 1.5 * min(3, f.greeting_agenda) + 2.5 * f.questions / n + 4.0 * f.profile_questions / n - 2.0 * f.first_person_money / n
    score += 0.4 if f.first_t <= earliest + 1e-6 else 0.0
    score += _identity(f, "advisor") - 0.8 * (_identity(f, "client_primary") + _identity(f, "client_secondary"))
    return score


def _squash(margin: float, lo: float = 0.5, hi: float = 0.97, k: float = 1.2) -> float:
    return round(lo + (hi - lo) * (1 - math.exp(-k * max(0.0, margin))), 3)


def heuristic_roles(segments: Sequence[Segment], crm: CrmNames | None = None, features: dict[str, SpeakerFeatures] | None = None) -> RoleAssignment:
    """Deterministic roles from behaviour and names (feature and fallback)."""
    feats = features or speaker_features(segments, crm)
    if not feats:
        return RoleAssignment(roles={}, confidence={}, rationale="no speakers")
    earliest = min(f.first_t for f in feats.values())
    ranked = sorted(feats.values(), key=lambda f: _advisor_score(f, earliest), reverse=True)
    roles: dict[str, SpeakerRole] = {}
    conf: dict[str, float] = {}
    top = ranked[0]
    margin = _advisor_score(top, earliest) - (_advisor_score(ranked[1], earliest) if len(ranked) > 1 else 0.0)
    if len(ranked) == 1:
        roles[top.label] = "advisor" if _advisor_score(top, earliest) > 1.0 else "client_primary"
        conf[top.label] = 0.5
        return RoleAssignment(roles=roles, confidence=conf, rationale="single speaker")
    roles[top.label] = "advisor"
    conf[top.label] = _squash(margin)
    rest = ranked[1:]
    if len(rest) == 1:
        roles[rest[0].label] = "client_primary"
        conf[rest[0].label] = conf[top.label]
    else:
        def primary_score(f: SpeakerFeatures) -> float:
            return _identity(f, "client_primary") - _identity(f, "client_secondary") + f.speech_s / max(1.0, sum(x.speech_s for x in rest))

        rest = sorted(rest, key=primary_score, reverse=True)
        named = any(_identity(f, r) > 0 for f in rest for r in ("client_primary", "client_secondary"))
        gap = primary_score(rest[0]) - primary_score(rest[1])
        for k, f in enumerate(rest):
            role: SpeakerRole = "client_primary" if k == 0 else "client_secondary" if k == 1 else "other"
            roles[f.label] = role
            if named and _identity(f, role) > 0:
                conf[f.label] = round(min(conf[top.label], 0.9), 3)
            elif role == "other":
                conf[f.label] = 0.5
            else:
                conf[f.label] = round(min(0.79, 0.55 + 0.4 * gap), 3)
    why = "; ".join(f"{f.label}: {f.summary()}" for f in ranked)
    return RoleAssignment(roles=roles, confidence=conf, rationale=f"heuristic: {why}")


def select_context(segments: Sequence[Segment], first_s: float = FIRST_WINDOW_S, sample: int = LATER_SAMPLE) -> list[Segment]:
    """The first five minutes plus an even sample of later turns, longest first within each stretch."""
    ordered = sorted(segments, key=lambda s: s.t_start)
    head = [s for s in ordered if s.t_start < first_s]
    later = [s for s in ordered if s.t_start >= first_s]
    if len(later) > sample:
        step = len(later) / sample
        later = [max(later[int(i * step) : int((i + 1) * step)] or [later[int(i * step)]], key=lambda s: len(s.words)) for i in range(sample)]
    return head + later


def _clock(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def build_prompt(segments: Sequence[Segment], crm: CrmNames | None, features: dict[str, SpeakerFeatures], heuristic: RoleAssignment) -> str:
    labels = sorted(features)
    lines = ["Speaker labels: " + ", ".join(labels), ""]
    if crm:
        lines += ["CRM names:", f"- adviser: {', '.join(crm.advisor) or 'unknown'}", f"- primary client: {', '.join(crm.client_primary) or 'unknown'}",
                  f"- second client: {', '.join(crm.client_secondary) or 'none on file'}", ""]
    lines.append("Per-speaker features:")
    lines += [f"- {lab}: {features[lab].summary()}" for lab in labels]
    lines.append("Heuristic guess: " + ", ".join(f"{lab}={heuristic.roles.get(lab, 'unknown')} ({heuristic.confidence.get(lab, 0):.2f})" for lab in labels))
    lines += ["", "Transcript excerpt (first five minutes, then a sample of later turns):"]
    body, used = [], 0
    for s in select_context(segments):
        line = f"[{s.segment_id} | {s.speaker_label} | {_clock(s.t_start)}] {s.text}"
        if used + len(line) > MAX_PROMPT_CHARS:
            break
        body.append(line)
        used += len(line)
    return "\n".join(lines + body)


def reconcile(output: RoleAssignmentOutput, labels: Sequence[str], heuristic: RoleAssignment) -> RoleAssignment:
    """Turn the agent's list into a valid ``RoleAssignment``: every label once, unique roles unique, disagreements held for review."""
    items = {i.speaker_label: i for i in output.speakers if i.speaker_label in labels}
    roles: dict[str, SpeakerRole] = {}
    conf: dict[str, float] = {}
    for lab in labels:
        if lab in items:
            roles[lab] = items[lab].role
            conf[lab] = min(1.0, max(0.0, float(items[lab].confidence)))
        else:
            roles[lab] = heuristic.roles.get(lab, "unknown")
            conf[lab] = round(0.7 * heuristic.confidence.get(lab, 0.5), 3)
    for role in UNIQUE_ROLES:
        holders = sorted((lab for lab in labels if roles[lab] == role), key=lambda lab: -conf[lab])
        for lab in holders[1:]:
            free = next((r for r in UNIQUE_ROLES if r != role and not any(roles[x] == r for x in labels)), None)
            roles[lab] = free if free and role != "advisor" and free != "advisor" else "other"
            conf[lab] = min(conf[lab], 0.5)
    for lab in labels:
        h_role, h_conf = heuristic.roles.get(lab), heuristic.confidence.get(lab, 0.0)
        if h_role and h_role != roles[lab] and h_conf >= MIN_ROLE_CONF:
            conf[lab] = min(conf[lab], round(MIN_ROLE_CONF - 0.01, 2))
    evidence = "; ".join(f"{lab}: {items[lab].evidence}" for lab in labels if lab in items)
    return RoleAssignment(roles=roles, confidence={k: round(v, 3) for k, v in conf.items()}, rationale=(output.rationale + (f" [{evidence}]" if evidence else "")).strip())


async def assign_roles(segments: Sequence[Segment], *, pins: VersionPins | None = None, crm: CrmNames | None = None,
                       use_agent: bool | None = None) -> RoleAssignment:
    """Roles for every speaker label. ``use_agent=None`` runs the agent unless the frontier pin is a test model."""
    feats = speaker_features(segments, crm)
    labels = sorted(feats)
    heuristic = heuristic_roles(segments, crm, feats)
    if use_agent is None:
        use_agent = not llm.is_test(llm.role_model_name("frontier", pins))
    if not use_agent or not labels:
        return heuristic
    rm = llm.for_role("frontier", pins)
    prompt = build_prompt(segments, crm, feats, heuristic)
    try:
        result = await role_agent.run(prompt, model=rm.model, model_settings=rm.settings)
    except UnexpectedModelBehavior as e:
        log.warning("role agent output failed validation, using the heuristic: %s", e)
        capped = {k: min(v, 0.6) for k, v in heuristic.confidence.items()}
        return heuristic.model_copy(update={"confidence": capped, "rationale": f"agent failed ({e}); {heuristic.rationale}"})
    return reconcile(result.output, labels, heuristic)


def apply_roles(segments: Sequence[Segment], assignment: RoleAssignment) -> list[Segment]:
    return [s.model_copy(update={"speaker_role": assignment.roles.get(s.speaker_label, "unknown"),
                                 "role_conf": assignment.confidence.get(s.speaker_label, 0.0)}) for s in segments]


def role_review_needed(assignment: RoleAssignment, labels: Sequence[str] | None = None) -> bool:
    labels = labels if labels is not None else list(assignment.roles)
    return any(assignment.confidence.get(lab, 0.0) < MIN_ROLE_CONF for lab in labels)
