"""An oracle model for end-to-end tests: answers every extraction stage from gold annotations.

It reads what each stage asks for from the compiled output schema (object lists, field keys,
instance enums) and answers from ``gold`` — so the whole pipeline after the model call
(validation, alignment, normalisation, the reducer, verification, VOI, the forecast, review) is
exercised for real. Fields the gold set doesn't name are ``not_discussed``.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic_ai.models.function import AgentInfo

from ..models.transcript import Transcript


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


class Oracle:
    def __init__(self, transcript: Transcript, objects: list[dict], gold: dict[str, dict]) -> None:
        self.t = transcript
        self.objects = objects
        self.gold = gold
        self.calls: list[str] = []

    # --------------------------------------------------------------- helpers

    def seg_ids(self, quotes: list[str] | str) -> list[str]:
        qs = [quotes] if isinstance(quotes, str) else quotes
        out = []
        for q in qs:
            for s in self.t.segments:
                if q.lower()[:40] in s.text.lower():
                    out.append(s.segment_id)
                    break
        return out

    def _gold_key(self, key: str) -> str | None:
        """Map a pipeline field key (instance = entity id) to a gold key (instance = '@name')."""
        if key in self.gold:
            return key
        slot, _, inst = key.partition("#")
        for g in self.gold:
            gslot, _, ginst = g.partition("#")
            if gslot == slot and ginst.startswith("@") and _slug(ginst[1:]) and _slug(ginst[1:]) in _slug(inst):
                return g
        return None

    @staticmethod
    def _defs(schema: dict, ref_holder: dict) -> dict:
        if "$ref" in ref_holder:
            return schema["$defs"][ref_holder["$ref"].rsplit("/", 1)[-1]]
        return ref_holder

    # --------------------------------------------------------------- stages

    def handler(self, prompt: str, schema: dict, info: AgentInfo) -> Any:
        props = schema.get("properties", {})
        if "objects" in props:
            self.calls.append("objects")
            return self.objects_answer(prompt)
        if "fields" in props and "order" not in props:
            self.calls.append("fields")
            item = self._defs(schema, props["fields"]["items"])
            return {"fields": [self.status_answer(k) for k in item["properties"]["fna_path"].get("enum", [])]}
        if "order" in props:
            return {"order": [], "rationale": "oracle plan"}
        self.calls.append("values")
        out: dict[str, list] = {}
        for prop, spec in props.items():
            item = self._defs(schema, spec["items"])
            insts = item["properties"]["instance"].get("enum", [])
            combined = "status" in item["properties"]
            out[prop] = [a for a in (self.value_answer(prop, i, combined) for i in insts) if a is not None]
        return out

    def objects_answer(self, prompt: str) -> dict:
        group = re.search(r"# Task \S+ — (\w+)", prompt)
        g = group.group(1) if group else ""
        out = []
        for o in self.objects:
            if o["group"] != g:
                continue
            item = {
                "entity_id": f"new:{_slug(o['name'])}",
                "entity_type": o["type"],
                "name": o["name"],
                "segment_ids": self.seg_ids(o["quotes"]),
            }
            if g == "expenses":
                item["catalog_key"] = o.get("catalog_key", "custom")
            out.append(item)
        return {"objects": out}

    def status_answer(self, key: str) -> dict:
        gk = self._gold_key(key)
        if gk is None:
            return {"fna_path": key, "status": "not_discussed", "segment_ids": []}
        g = self.gold[gk]
        return {"fna_path": key, "status": g.get("status", "stated"), "segment_ids": self.seg_ids(g.get("quotes", g["said"]))}

    def value_answer(self, prop: str, instance: str, combined: bool) -> dict | None:
        # prop is the compiled key of the slot (e.g. income_p_grossMonthly); rebuild candidate field keys.
        for gk, g in self.gold.items():
            slot, _, ginst = gk.partition("#")
            if re.sub(r"[^A-Za-z0-9]+", "_", slot.replace("{p}", "p")).strip("_") != prop:
                continue
            inst_ok = (
                ginst == instance or (ginst.startswith("@") and _slug(ginst[1:]) in _slug(instance)) or (not ginst and instance == "-")
            )
            if not inst_ok:
                continue
            hyps = []
            for f in g["forms"]:
                form = {k: v for k, v in f.items() if not k.startswith("_")}
                if "currency" in form and form["type"] != "AMOUNT_RELATIVE":
                    form.setdefault("priced_year", 0)  # the strict schema requires it; gold omits it when the amount is undated
                hyps.append(
                    {"intermediate": form, "weight": f.get("_weight", 0), "rationale": "as said", "revises": bool(f.get("_revises", False))}
                )
            ans = {
                "instance": instance,
                "hypotheses": hyps,
                "hedges": g.get("hedges", []),
                "speaker_attribution": g.get("attribution", "client"),
                "said": g["said"],
                "interpretation": g.get("interpretation", "as the client said"),
                "self_score": g.get("self_score", 0.9),
            }
            if combined:
                ans.update(status=g.get("status", "stated"), segment_ids=self.seg_ids(g.get("quotes", g["said"])))
            return ans
        if combined:
            return {
                "instance": instance,
                "hypotheses": [],
                "hedges": [],
                "speaker_attribution": "client",
                "said": "",
                "interpretation": "",
                "self_score": 0.0,
                "status": "not_discussed",
                "segment_ids": [],
            }
        return None
