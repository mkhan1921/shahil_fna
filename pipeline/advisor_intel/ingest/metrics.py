"""Stage 1 metrics (§15.3), as plain functions for the Braintrust scorers in ``evals/``.

- ``numeric_wer``: word error rate restricted to numeric and financial-term tokens (target ≤ 8 %). Both sides
  are canonicalised first: lowercase, no punctuation, written numbers spelled out ("R250 000" → "two hundred
  and fifty thousand rand"), and "and" inside numbers dropped, so ITN style is not counted as an error. A
  reference token is a target when it is a number, scale, currency, rate or period word, or sits inside an
  amount, age or product span the grammars find in the reference. Errors are substitutions and deletions
  of target tokens plus insertions of numeric or financial tokens; the denominator is the target count.
- ``der``: diarization error rate (missed speech + false alarm + speaker confusion) / reference speech, on
  10 ms frames with the optimal one-to-one speaker mapping and a no-score collar around reference
  boundaries (default ±0.25 s; target ≤ 10 % mono).
- ``speaker_role_accuracy``: share of reference speech time whose transcript segment carries the gold role
  (target ≥ 98 %); ``label_role_accuracy`` compares label → role maps directly.
- ``numeric_span_accuracy``: for gold amounts with times, whether the overlapping span's top reading (and
  any reading) has the gold value — the bake-off's numeric-span accuracy (§17).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from rapidfuzz.distance import Levenshtein
from scipy.optimize import linear_sum_assignment

from ..models.transcript import Transcript
from .grammars import number_to_words, parse
from .grammars.amount import CURRENCY_WORDS, PERIOD_WORDS, scale_vocabulary
from .grammars.numbers import number_vocabulary
from .grammars.tokens import normalize_text
from .spans import find_in_tokens, tok_from_text
from .types import SpeakerTurn

_DIGITS = re.compile(r"\d")
_NUMBER_WORDS = number_vocabulary()
_SCALE_WORDS = scale_vocabulary()
_PERIOD_TOKENS = frozenset(t for w in PERIOD_WORDS for t in w.split()) - {"a", "per", "'n", "n", "in", "one", "each", "every", "elke"}
_CURRENCY_NAMES = {"ZAR": "rand", "USD": "dollars", "GBP": "pounds", "EUR": "euros"}
_NUMERIC = _NUMBER_WORDS | _SCALE_WORDS | _PERIOD_TOKENS | {"percent", "point", "comma", "half", "quarter"}


def _spell_digits(tokens: list[str]) -> list[str]:
    """Replace runs of written numbers ("r250", "000", "11.5%") with their spoken words."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if not _DIGITS.search(tokens[i]):
            out.append(tokens[i])
            i += 1
            continue
        j = i + 1
        while j < len(tokens) and re.fullmatch(r"\d{3}", tokens[j]):
            j += 1
        res = parse(" ".join(tokens[i:j]), "amount")
        literal = [r for r in res.readings if not (r.scale_source or "").startswith("implied")]
        if not literal:
            out += tokens[i:j]
        else:
            r = literal[0]
            out += number_to_words(float(r.value)).split()
            if r.is_rate:
                out.append("percent")
            elif r.currency:
                out.append(_CURRENCY_NAMES.get(r.currency, r.currency.lower()))
        i = j
    return out


def canonical_tokens(text: str) -> list[str]:
    """Tokens for WER: normalised, numbers spelled out, "and" inside numbers dropped."""
    toks = _spell_digits(normalize_text(text).split())
    out = []
    for k, t in enumerate(toks):
        if t in ("and", "en") and 0 < k < len(toks) - 1 and toks[k - 1] in _NUMBER_WORDS and toks[k + 1] in _NUMERIC:
            continue
        out.append(t)
    return out


def _target_mask(tokens: list[str]) -> list[bool]:
    mask = [t in _NUMERIC or t in CURRENCY_WORDS for t in tokens]
    for f in find_in_tokens([tok_from_text(t) for t in tokens]):
        for k in range(f.start, f.end):
            mask[k] = True
    return mask


def _edits(ref: list[str], hyp: list[str]) -> list[tuple[str, int, int]]:
    """Word-level Levenshtein edit operations ('sub'|'del'|'ins', ref index, hyp index), via rapidfuzz."""
    names = {"replace": "sub", "delete": "del", "insert": "ins"}
    return [(names[op.tag], op.src_pos, op.dest_pos) for op in Levenshtein.editops(ref, hyp)]


@dataclass
class WerResult:
    wer: float  # numeric and financial-term WER
    targets: int
    substitutions: int
    deletions: int
    insertions: int
    overall_wer: float  # plain WER over every token, for context
    errors: list[tuple[str, str, str]] = field(default_factory=list)  # (op, ref, hyp)


def numeric_wer(reference: str, hypothesis: str) -> WerResult:
    ref, hyp = canonical_tokens(reference), canonical_tokens(hypothesis)
    mask = _target_mask(ref)
    ops = _edits(ref, hyp)
    s = dl = ins = 0
    errors = []
    for op, i, j in ops:
        if op == "sub" and mask[i]:
            s += 1
            errors.append((op, ref[i], hyp[j]))
        elif op == "del" and mask[i]:
            dl += 1
            errors.append((op, ref[i], ""))
        elif op == "ins" and (hyp[j] in _NUMERIC or bool(_DIGITS.search(hyp[j]))):
            ins += 1
            errors.append((op, "", hyp[j]))
    targets = sum(mask)
    overall = len(ops) / max(1, len(ref))
    return WerResult(
        wer=(s + dl + ins) / targets if targets else 0.0,
        targets=targets,
        substitutions=s,
        deletions=dl,
        insertions=ins,
        overall_wer=overall,
        errors=errors,
    )


@dataclass
class DerResult:
    der: float
    missed: float  # seconds
    false_alarm: float
    confusion: float
    total: float  # scored reference speech, seconds
    mapping: dict[str, str]  # reference speaker → hypothesis speaker


def der(
    reference: Sequence[SpeakerTurn],
    hypothesis: Sequence[SpeakerTurn],
    *,
    collar: float = 0.25,
    frame: float = 0.01,
    skip_overlap: bool = False,
) -> DerResult:
    end = max([t.end for t in [*reference, *hypothesis]], default=0.0)
    n = int(math.ceil(end / frame)) + 1
    ref_spk = sorted({t.speaker for t in reference})
    hyp_spk = sorted({t.speaker for t in hypothesis})
    R = np.zeros((len(ref_spk), n), dtype=bool)
    H = np.zeros((len(hyp_spk), n), dtype=bool)
    scored = np.ones(n, dtype=bool)
    for t in reference:
        R[ref_spk.index(t.speaker), int(round(t.start / frame)) : int(round(t.end / frame))] = True
        if collar > 0:
            for b in (t.start, t.end):
                scored[max(0, int(round((b - collar) / frame))) : int(round((b + collar) / frame))] = False
    for t in hypothesis:
        H[hyp_spk.index(t.speaker), int(round(t.start / frame)) : int(round(t.end / frame))] = True
    if skip_overlap:
        scored &= R.sum(axis=0) <= 1
    Rs, Hs = R[:, scored], H[:, scored]
    mapping: dict[str, str] = {}
    pairs: list[tuple[int, int]] = []
    if ref_spk and hyp_spk:
        overlap = (Rs[:, None, :] & Hs[None, :, :]).sum(axis=2)
        rows, cols = linear_sum_assignment(-overlap)
        pairs = [(r, c) for r, c in zip(rows, cols) if overlap[r, c] > 0]
        mapping = {ref_spk[r]: hyp_spk[c] for r, c in pairs}
    n_ref, n_hyp = Rs.sum(axis=0), Hs.sum(axis=0)
    correct = np.zeros(Rs.shape[1], dtype=int)
    for r, c in pairs:
        correct += Rs[r] & Hs[c]
    missed = np.maximum(0, n_ref - n_hyp).sum() * frame
    fa = np.maximum(0, n_hyp - n_ref).sum() * frame
    conf = (np.minimum(n_ref, n_hyp) - correct).sum() * frame
    total = n_ref.sum() * frame
    return DerResult(
        der=(missed + fa + conf) / total if total else 0.0,
        missed=round(missed, 3),
        false_alarm=round(fa, 3),
        confusion=round(conf, 3),
        total=round(total, 3),
        mapping=mapping,
    )


def speaker_role_accuracy(gold: Sequence[tuple[float, float, str]], transcript: Transcript) -> float:
    """Share of gold speech time (start, end, role) covered by segments with the same role."""
    total = right = 0.0
    for start, end, role in gold:
        total += max(0.0, end - start)
        for s in transcript.segments:
            ov = min(end, s.t_end) - max(start, s.t_start)
            if ov > 0 and s.speaker_role == role:
                right += ov
    return right / total if total else 0.0


def label_role_accuracy(gold: dict[str, str], predicted: dict[str, str]) -> float:
    return sum(1 for k, v in gold.items() if predicted.get(k) == v) / len(gold) if gold else 0.0


@dataclass
class SpanAccuracy:
    top1: float  # gold value is the top reading
    recall: float  # gold value is among the readings
    found: float  # a span overlaps the gold amount at all
    n: int


def numeric_span_accuracy(gold: Sequence[tuple[float, float, float]], transcript: Transcript, *, rel_tol: float = 1e-6) -> SpanAccuracy:
    """Gold amounts as (t_start, t_end, value) against the transcript's amount spans."""
    from .spans import span_interval

    spans = [(span_interval(transcript, s), s) for s in transcript.numeric_spans if s.kind == "amount"]
    top = rec = found = 0
    for t0, t1, value in gold:
        hits = [s for (a, b), s in spans if b > t0 and a < t1]
        if not hits:
            continue
        found += 1
        if any(s.readings and math.isclose(float(s.readings[0].value), value, rel_tol=rel_tol) for s in hits):
            top += 1
        if any(any(math.isclose(float(r.value), value, rel_tol=rel_tol) for r in s.readings) for s in hits):
            rec += 1
    n = len(gold)
    return SpanAccuracy(top1=top / n if n else 0.0, recall=rec / n if n else 0.0, found=found / n if n else 0.0, n=n)
