"""ScriptedASR: deterministic, realistic word-level output from a scripted call."""

from __future__ import annotations

from itertools import pairwise

import pytest

from advisor_intel.ingest.asr import ASRProvider, NoiseConfig, ScriptedASR, ScriptedCall, ScriptTurn

SCRIPT = """
# a short review call
advisor: Morning Thabo. What do you take home each month?
client: About {forty two|means=42000} thousand, and the bond is {two fifty|heard=two fifteen|means=250000}.
[pause 2.0]
advisor: And the RA? Is that with Allan Gray?
[overlap 0.5]
client: Yes.
advisor: Fifteen hundred a month for the medical aid, sixty, seventy, eighty, ninety.
"""


def test_render_is_deterministic_for_a_seed():
    noise = NoiseConfig(mishear_rate=0.5, low_conf_number_rate=0.5, crosstalk_rate=0.5, long_pause_rate=0.5)
    a = ScriptedASR(SCRIPT, seed=7, noise=noise).render()
    b = ScriptedASR(SCRIPT, seed=7, noise=noise).render()
    c = ScriptedASR(SCRIPT, seed=8, noise=noise).render()
    assert a.model_dump() == b.model_dump()
    assert a.model_dump() != c.model_dump()


def test_parse_directives_and_speakers():
    call = ScriptedCall.parse(SCRIPT, roles={"advisor": "advisor", "client": "client_primary"})
    assert [t.speaker for t in call.turns] == ["advisor", "client", "advisor", "client", "advisor"]
    assert call.turns[2].gap_before == 2.0 and call.turns[3].overlap == 0.5
    assert call.speakers() == ["advisor", "client"] and not call.stereo
    with pytest.raises(ValueError):
        ScriptedCall.parse("no speaker prefix here")


async def test_words_timing_markup_and_reference():
    asr = ScriptedASR(SCRIPT, seed=1)
    assert isinstance(asr, ASRProvider)
    res = await asr.transcribe(None, None)
    r = asr.render()
    assert res.time_base == "call" and res.alternatives_available and res.provider == "scripted"
    words = res.words
    assert all(w.t_end > w.t_start for w in words)
    assert all(a.t_start <= b.t_start for a, b in pairwise(words))
    texts = [w.text for w in words]
    assert "fifteen." in texts and "fifty." not in texts  # the markup mishearing
    fifteen = words[texts.index("fifteen.")]
    assert fifteen.conf < 0.7 and fifteen.alts and fifteen.alts[0][0] == "fifty."
    assert ("two fifty.", "two fifteen.") in r.misheard
    ref = [w.text for w in r.reference]
    assert "fifty." in ref and "fifteen." not in ref
    assert [i.value for i in r.intended] == [42000.0, 250000.0]
    # pause directive: at least 2 s of silence before the third turn
    assert r.turns[2].start - r.turns[1].end >= 2.0 - 1e-6
    # overlap directive: the fourth turn starts before the third ends
    assert r.turns[3].start < r.turns[2].end
    assert {w.speaker for w in words} == {"advisor", "client"}
    assert r.reference_turns()[0].speaker == "advisor"
    assert "Allan Gray" in r.reference_text()


def test_homophone_and_number_alternatives():
    r = ScriptedASR("client: I pay for two of them, fifty thousand.", seed=2).render()
    by = {w.text.strip(",."): w for w in r.words}
    assert by["for"].alts[0][0] == "four" and by["for"].alts[0][1] < 0.2
    assert by["fifty"].alts[0][0] == "fifteen"
    assert by["thousand"].alts[0][1] < 0.05  # scale confusions are rare
    none = ScriptedASR("client: I pay for two of them, fifty thousand.", seed=2).render(want_alternatives=False)
    assert all(w.alts == [] for w in none.words)


def test_mishearing_noise_keeps_truth_in_alternatives():
    script = "client: " + " ".join(["fifty sixty seventy forty"] * 10)
    r = ScriptedASR(script, seed=3, noise=NoiseConfig(mishear_rate=1.0)).render()
    assert len(r.misheard) == 40
    for w, said in zip(r.words, [x.text for x in r.reference]):
        assert w.text != said and w.alts[0][0] == said and w.conf < 0.7


def test_low_confidence_numbers_and_speaker_shift():
    script = "client: " + " ".join(["two hundred and fifty thousand rand"] * 20) + "\nadvisor: " + " ".join(["thank you"] * 20)
    r = ScriptedASR(script, seed=4, noise=NoiseConfig(low_conf_number_rate=1.0, speaker_conf_shift={"advisor": -0.2})).render()
    numbers = [w for w in r.words if w.text.strip(".,") in ("two", "hundred", "fifty", "thousand")]
    assert all(0.45 <= w.conf <= 0.72 for w in numbers)
    adv = [w.conf for w in r.words if w.speaker == "advisor"]
    assert sum(adv) / len(adv) < 0.8


def test_itn_emulation_collapses_numbers_and_keeps_the_spoken_form():
    r = ScriptedASR(
        "client: We pay two hundred and fifty thousand rand a month and eleven and a half percent on the bond.",
        seed=5,
        noise=NoiseConfig(itn=True),
    ).render()
    itn = [w for w in r.words if w.raw_spoken]
    assert [w.text for w in itn] == ["R250 000", "11.5%"]
    assert itn[0].raw_spoken == "two hundred and fifty thousand rand" and itn[0].alts == []
    assert [w.text for w in r.words][-3:] == ["on", "the", "bond."]
    assert "a" in [w.text for w in r.words] and "month" in [w.text for w in r.words]


def test_stereo_channels_and_crosstalk_noise():
    call = ScriptedCall(
        turns=[
            ScriptTurn(speaker="advisor", text="So how much do you put away each month for the kids?", channel=0),
            ScriptTurn(speaker="client", text="Mm-hm.", channel=1),
            ScriptTurn(speaker="client", text="About two thousand.", channel=1),
        ]
    )
    r = ScriptedASR(call, seed=1, noise=NoiseConfig(crosstalk_rate=1.0)).render()
    assert {w.channel for w in r.words if w.speaker == "advisor"} == {0}
    assert {w.channel for w in r.words if w.speaker == "client"} == {1}
    assert r.turns[1].start < r.turns[0].end  # the short backchannel overlaps
    assert call.stereo


def test_long_pause_noise_inserts_gaps_over_a_second():
    text = "advisor: " + " ".join(["This is a sentence."] * 12)
    r = ScriptedASR(text, seed=2, noise=NoiseConfig(long_pause_rate=1.0)).render()
    gaps = [b.t_start - a.t_end for a, b in zip(r.words, r.words[1:])]
    assert sum(g > 1.2 for g in gaps) == 11
