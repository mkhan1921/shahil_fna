"""``ScriptedASR``: a deterministic fake provider that turns a scripted call into realistic word-level ASR output.

A script is a list of turns (speaker, text, optional channel). Rendering it gives each word a timestamp
(syllable-based durations, pauses at punctuation, gaps or deliberate overlaps between turns), a confidence,
and per-word alternatives, then applies seeded noise: low-confidence numbers, plausible mishearings of
number words ("fifty" heard as "fifteen", with the true word kept in the alternatives), homophone
alternatives ("for" / "four"), crosstalk and long pauses. With ``itn=True`` number phrases are collapsed
the way a provider's inverse text normalisation would ("two hundred and fifty thousand" → "250 000" with
``raw_spoken`` kept). The same script, seed and settings always render identically.

Inline markup pins a word's behaviour: ``{fifty|heard=fifteen|conf=0.55|alts=fifty:0.4;sixty:0.1}``.
``heard`` replaces the word(s) in the 1-best ("" deletes them), ``conf`` sets the confidence and ``alts``
the alternatives (``text:score`` pairs separated by ``;``).

Scripts can also be written as text, one turn per line: ``advisor: Good morning…``; ``client@1: …`` puts
the turn on channel 1; ``[pause 2.5]`` adds silence before the next turn and ``[overlap 0.6]`` starts the
next turn 0.6 s before the previous one ends.
"""

from __future__ import annotations

import random
import re
from functools import cached_property

from pydantic import BaseModel, Field

from ...models.transcript import SpeakerRole
from ..grammars import parse as grammar_parse
from ..grammars import written_amount
from ..grammars.amount import PERIOD_WORDS
from ..grammars.tokens import normalize_token
from ..spans import find_in_tokens, tok_from_text
from ..types import AsrResult, AsrWord, SpeakerTurn, Vocabulary
from .base import AsrAudio

MARKUP = re.compile(r"\{([^{}|]+)((?:\|[^{}|]*)*)\}")
SENTENCE_END = (".", "?", "!")
VOWELS = re.compile(r"[aeiouyëéè]+")

# Classic mishearings of number words (the "two fifty" ↔ "two fifteen" family), both ways.
_PAIRS = [
    ("fifty", "fifteen"),
    ("forty", "fourteen"),
    ("sixty", "sixteen"),
    ("seventy", "seventeen"),
    ("eighty", "eighteen"),
    ("ninety", "nineteen"),
    ("thirty", "thirteen"),
    ("vyftig", "vyftien"),
    ("sestig", "sestien"),
    ("veertig", "veertien"),
]
CONFUSIONS: dict[str, list[str]] = {}
for _a, _b in _PAIRS:
    CONFUSIONS.setdefault(_a, []).append(_b)
    CONFUSIONS.setdefault(_b, []).append(_a)
CONFUSIONS.update(
    {"thousand": ["thousands"], "million": ["billion"], "hundred": ["hundreds"], "two": ["to"], "four": ["for"], "eight": ["ate"]}
)
RARE_CONFUSIONS = {"million", "billion", "thousand", "hundred"}  # scale words: their confusions get small alternative scores
HOMOPHONES = {
    "for": "four",
    "four": "for",
    "to": "two",
    "too": "two",
    "two": "to",
    "one": "won",
    "won": "one",
    "eight": "ate",
    "ate": "eight",
    "their": "there",
    "there": "their",
    "by": "buy",
    "know": "no",
    "no": "know",
    "our": "hour",
    "hour": "our",
    "some": "sum",
    "sum": "some",
    "right": "write",
    "new": "knew",
    "week": "weak",
    "rand": "ran",
    "bond": "bomb",
}
MISHEARABLE = {k for k, _ in _PAIRS} | {v for _, v in _PAIRS}


class ScriptTurn(BaseModel):
    speaker: str
    text: str
    channel: int | None = None
    gap_before: float | None = None  # silence before the turn (default: sampled 0.25–0.8 s)
    overlap: float | None = None  # start this many seconds before the previous turn ends (crosstalk)
    rate: float = 1.0  # speaking-rate multiplier


class ScriptedCall(BaseModel):
    turns: list[ScriptTurn]
    roles: dict[str, SpeakerRole] = {}  # gold roles per script speaker
    lead_in: float = 0.6  # seconds before the first word

    @classmethod
    def parse(cls, text: str, roles: dict[str, SpeakerRole] | None = None) -> ScriptedCall:
        """Parse ``speaker[@channel]: text`` lines, with ``[pause s]`` and ``[overlap s]`` directives."""
        turns: list[ScriptTurn] = []
        pending: dict = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = re.fullmatch(r"\[(pause|overlap)\s+([\d.]+)\]", line)
            if m:
                pending["gap_before" if m.group(1) == "pause" else "overlap"] = float(m.group(2))
                continue
            head, sep, body = line.partition(":")
            if not sep:
                raise ValueError(f"script line has no 'speaker:' prefix: {line!r}")
            speaker, _, chan = head.strip().partition("@")
            turns.append(ScriptTurn(speaker=speaker.strip(), text=body.strip(), channel=int(chan) if chan else None, **pending))
            pending = {}
        return cls(turns=turns, roles=roles or {})

    def speakers(self) -> list[str]:
        seen: list[str] = []
        for t in self.turns:
            if t.speaker not in seen:
                seen.append(t.speaker)
        return seen

    @property
    def stereo(self) -> bool:
        return any(t.channel is not None for t in self.turns)


class NoiseConfig(BaseModel):
    """Seeded noise knobs (all off by default except realistic confidence spread and homophone alternatives)."""

    base_conf: float = 0.93
    conf_sd: float = 0.035
    min_conf: float = 0.55
    low_conf_number_rate: float = 0.0  # share of number words given a low confidence (0.45–0.72)
    mishear_rate: float = 0.0  # share of confusable number words replaced by their confusion
    homophone_alts: bool = True
    number_alts: bool = True  # confusable number words carry their confusion as a low-score alternative
    crosstalk_rate: float = 0.0  # share of short turns (≤ 4 words) that overlap the previous turn
    long_pause_rate: float = 0.0  # share of sentence ends followed by a 1.3–2.2 s pause
    itn: bool = False  # collapse number phrases into written form, keeping raw_spoken
    speaker_conf_shift: dict[str, float] = {}  # e.g. a client on speaker-phone: {"client": -0.08}
    words_per_second: float = 2.6


class RefWord(BaseModel):
    text: str
    t_start: float
    t_end: float
    speaker: str
    turn: int
    channel: int | None = None


class RefTurn(BaseModel):
    speaker: str
    start: float
    end: float
    text: str
    turn: int
    channel: int | None = None


class Intended(BaseModel):
    """What a phrase means, from ``{…|means=…}`` markup: the value a listener with context would pick."""

    t_start: float
    t_end: float
    said: str
    value: float | str


class ScriptRender(BaseModel):
    """What the fake provider hears (``words``) and the truth it was scripted from."""

    words: list[AsrWord]
    reference: list[RefWord]
    turns: list[RefTurn]
    speakers: list[str]
    duration: float
    roles: dict[str, SpeakerRole] = {}
    misheard: list[tuple[str, str]] = Field(default_factory=list)  # (said, heard) pairs the noise introduced
    intended: list[Intended] = Field(default_factory=list)

    def reference_turns(self) -> list[SpeakerTurn]:
        return [SpeakerTurn(speaker=t.speaker, start=t.start, end=t.end, channel=t.channel) for t in self.turns]

    def reference_text(self, t0: float = 0.0, t1: float = float("inf")) -> str:
        return " ".join(w.text for w in self.reference if w.t_end > t0 and w.t_start < t1)


def _syllables(word: str) -> int:
    w = normalize_token(word)
    if any(c.isdigit() for c in w):
        return max(2, 2 * len([c for c in w if c.isdigit()]) // 2)
    return max(1, len(VOWELS.findall(w)))


def _parse_markup(text: str) -> list[tuple[str, dict]]:
    """Split a turn into (word, overrides) pairs; markup groups expand to their words.

    Punctuation written straight after a group ("{two fifty|…}, and") stays on the group's last word.
    """
    out: list[tuple[str, dict]] = []
    pos = 0
    for m in MARKUP.finditer(text):
        before = text[pos : m.start()]
        if out and before and not before[0].isspace():
            head, _, before = before.partition(" ")
            said, opts = out[-1]
            out[-1] = (said + head, {**opts, "punct": opts.get("punct", "") + head} if opts.get("group") else opts)
        out += [(w, {}) for w in before.split()]
        opts: dict = {}
        for part in filter(None, m.group(2).split("|")):
            k, _, v = part.partition("=")
            k = k.strip()
            if k == "conf":
                opts["conf"] = float(v)
            elif k == "means":
                opts["means"] = float(v) if re.fullmatch(r"[\d.]+", v.strip()) else v.strip()
            elif k == "heard":
                opts["heard"] = v.strip()
            elif k == "alts":
                opts["alts"] = [(a.rsplit(":", 1)[0].strip(), float(a.rsplit(":", 1)[1])) for a in v.split(";") if a.strip()]
            else:
                raise ValueError(f"unknown markup key {k!r} in {m.group(0)!r}")
        words = m.group(1).split()
        if "heard" in opts or "alts" in opts or "means" in opts:
            out.append((" ".join(words), {**opts, "group": len(words)}))
        else:
            out += [(w, dict(opts)) for w in words]
        pos = m.end()
    rest = text[pos:]
    if out and rest and not rest[0].isspace():
        head, _, rest = rest.partition(" ")
        said, opts = out[-1]
        out[-1] = (said + head, {**opts, "punct": opts.get("punct", "") + head} if opts.get("group") else opts)
    out += [(w, {}) for w in rest.split()]
    return out


class ScriptedASR:
    """A fake ``ASRProvider`` that renders a script; deterministic for a given seed."""

    name = "scripted"

    def __init__(self, script: ScriptedCall | str, *, seed: int = 0, noise: NoiseConfig | None = None, model_version: str = "scripted-1"):
        self.script = ScriptedCall.parse(script) if isinstance(script, str) else script
        self.seed = seed
        self.noise = noise or NoiseConfig()
        self.model_version = model_version
        self._renders: dict[bool, ScriptRender] = {}

    def render(self, want_alternatives: bool = True) -> ScriptRender:
        if want_alternatives not in self._renders:
            self._renders[want_alternatives] = self._render(want_alternatives)
        return self._renders[want_alternatives]

    @cached_property
    def reference(self) -> ScriptRender:
        return self.render(True)

    async def transcribe(self, audio: AsrAudio | None = None, vocab: Vocabulary | None = None, want_alternatives: bool = True) -> AsrResult:
        r = self.render(want_alternatives)
        return AsrResult(
            words=r.words,
            provider=self.name,
            model_version=self.model_version,
            alternatives_available=want_alternatives,
            language="en-ZA",
            time_base="call",
            duration=r.duration,
        )

    # ------------------------------------------------------------------ rendering

    def _conf(self, rng: random.Random, speaker: str) -> float:
        n = self.noise
        c = rng.gauss(n.base_conf + n.speaker_conf_shift.get(speaker, 0.0), n.conf_sd)
        return round(min(0.995, max(n.min_conf, c)), 3)

    def _render(self, want_alts: bool) -> ScriptRender:
        rng = random.Random(f"{self.seed}:{want_alts}")
        n = self.noise
        words: list[AsrWord] = []
        reference: list[RefWord] = []
        turns: list[RefTurn] = []
        misheard: list[tuple[str, str]] = []
        intended: list[Intended] = []
        prev_start, prev_end, call_end = 0.0, self.script.lead_in, self.script.lead_in
        for ti, turn in enumerate(self.script.turns):
            items = _parse_markup(turn.text)
            if not items:
                continue
            gap = turn.gap_before if turn.gap_before is not None else rng.uniform(0.25, 0.8)
            overlap = turn.overlap
            if overlap is None and n.crosstalk_rate and len(items) <= 4 and ti and rng.random() < n.crosstalk_rate:
                overlap = rng.uniform(0.35, 0.8)
            if overlap is not None and ti:
                t = max(prev_start + 0.1, prev_end - overlap)
            else:
                t = max(call_end, prev_end) + gap
            start = t
            speed = n.words_per_second / 2.6 * turn.rate
            turn_words: list[AsrWord] = []
            for k, (said, opts) in enumerate(items):
                dur = (0.09 + 0.16 * sum(_syllables(w) for w in said.split())) / speed * rng.uniform(0.9, 1.1)
                t0, t1 = t, t + dur
                if "means" in opts:
                    intended.append(Intended(t_start=round(t0, 3), t_end=round(t1, 3), said=said, value=opts["means"]))
                for j, w in enumerate(said.split()):
                    share = dur / max(1, len(said.split()))
                    reference.append(
                        RefWord(
                            text=w,
                            t_start=round(t0 + j * share, 3),
                            t_end=round(t0 + (j + 1) * share, 3),
                            speaker=turn.speaker,
                            turn=ti,
                            channel=turn.channel,
                        )
                    )
                heard, conf, alts = self._hear(rng, said, opts, turn.speaker, want_alts, misheard)
                hw = heard.split()
                sw = said.split()
                for j, w in enumerate(hw):
                    share = dur / len(hw)
                    if len(hw) == 1:
                        word_alts = alts
                    elif len(hw) == len(sw) and "alts" not in opts:  # a misheard group: the said word is the alternative where they differ
                        word_alts = [(sw[j], s) for _, s in alts[:1]] if want_alts and normalize_token(sw[j]) != normalize_token(w) else []
                    else:
                        word_alts = []
                    turn_words.append(
                        AsrWord(
                            text=w,
                            t_start=round(t0 + j * share, 3),
                            t_end=round(t0 + (j + 1) * share - 0.01, 3),
                            conf=conf,
                            alts=word_alts,
                            channel=turn.channel,
                            speaker=turn.speaker,
                        )
                    )
                pause = rng.uniform(0.02, 0.08)
                if said.endswith(","):
                    pause += rng.uniform(0.12, 0.25)
                if said.endswith(SENTENCE_END) and k < len(items) - 1:
                    pause += rng.uniform(0.25, 0.45)
                    if n.long_pause_rate and rng.random() < n.long_pause_rate:
                        pause += rng.uniform(1.3, 2.2)
                t = t1 + pause
            end = round(t, 3)
            if n.itn:
                turn_words = self._itn(turn_words)
            words += turn_words
            turns.append(
                RefTurn(
                    speaker=turn.speaker,
                    start=round(start, 3),
                    end=end,
                    text=re.sub(MARKUP, lambda m: m.group(1), turn.text),
                    turn=ti,
                    channel=turn.channel,
                )
            )
            prev_start, prev_end = start, end
            call_end = max(call_end, end)
        words.sort(key=lambda w: (w.t_start, w.channel or 0))
        return ScriptRender(
            words=words,
            reference=reference,
            turns=turns,
            speakers=self.script.speakers(),
            duration=round(call_end + 0.5, 3),
            roles=dict(self.script.roles),
            misheard=misheard,
            intended=intended,
        )

    def _hear(
        self, rng: random.Random, said: str, opts: dict, speaker: str, want_alts: bool, misheard: list[tuple[str, str]]
    ) -> tuple[str, float, list[tuple[str, float]]]:
        n = self.noise
        norm = normalize_token(said)
        conf = self._conf(rng, speaker)
        heard = said
        alts: list[tuple[str, float]] = []
        is_number = norm in MISHEARABLE or norm in CONFUSIONS
        if "heard" in opts:
            heard = opts["heard"] + (opts.get("punct", "") if opts["heard"] else "")
            if normalize_token(heard) != norm:
                misheard.append((said, heard))
                conf = min(conf, round(rng.uniform(0.42, 0.68), 3))
                alts = [(said, round(conf * rng.uniform(0.6, 0.95), 3))]
        elif norm in MISHEARABLE and n.mishear_rate and rng.random() < n.mishear_rate:
            heard = CONFUSIONS[norm][0]
            misheard.append((said, heard))
            conf = round(rng.uniform(0.42, 0.68), 3)
            alts = [(norm, round(conf * rng.uniform(0.6, 0.95), 3))]
        elif is_number and n.low_conf_number_rate and rng.random() < n.low_conf_number_rate:
            conf = round(rng.uniform(0.45, 0.72), 3)
        if "conf" in opts:
            conf = opts["conf"]
        if "alts" in opts:
            alts = list(opts["alts"])
        elif heard == said and want_alts:
            if n.number_alts and norm in CONFUSIONS:
                lo, hi = (0.005, 0.03) if norm in RARE_CONFUSIONS else (0.05, 0.2)
                alts = [(c, round(rng.uniform(lo, hi) * (1.6 - conf), 3)) for c in CONFUSIONS[norm]]
            elif n.homophone_alts and norm in HOMOPHONES:
                alts = [(HOMOPHONES[norm], round(rng.uniform(0.04, 0.15), 3))]
        if not want_alts:
            alts = []
        return heard, conf, [(a, min(0.99, max(0.001, s))) for a, s in alts]

    def _itn(self, words: list[AsrWord]) -> list[AsrWord]:
        """Collapse number phrases into written form, as a provider's ITN would (taking the literal reading)."""
        if not words:
            return words
        toks = [tok_from_text(w.text, w.conf) for w in words]
        out: list[AsrWord] = []
        i = 0
        for f in find_in_tokens(toks):
            if f.kind != "amount" or not f.accepted:
                continue
            end = f.end
            while end - 1 > f.start and normalize_token(words[end - 1].text) in {w.split()[-1] for w in PERIOD_WORDS} | {"a", "per"}:
                end -= 1
            spoken = " ".join(w.text.strip(",.?!") for w in words[f.start : end])
            res = grammar_parse(spoken, "amount")
            explicit = [r for r in res.readings if not (r.scale_source or "").startswith("implied")]
            if not explicit or res.readings[0].period != "none":
                continue
            r = explicit[0]
            text = f"{float(r.value):g}%" if r.is_rate else written_amount(float(r.value), r.currency)
            out += words[i : f.start]
            span = words[f.start : end]
            out.append(
                AsrWord(
                    text=text,
                    t_start=span[0].t_start,
                    t_end=span[-1].t_end,
                    conf=round(sum(w.conf for w in span) / len(span), 3),
                    alts=[],
                    raw_spoken=spoken,
                    channel=span[0].channel,
                    speaker=span[0].speaker,
                )
            )
            i = end
        out += words[i:]
        return out
