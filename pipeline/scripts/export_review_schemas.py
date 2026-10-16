"""Export the review contracts to the FNA app: JSON Schemas and the slot catalog (spec §11, §15.2).

The app's review overlay (``lib/review/``) types the contracts in TypeScript by hand. Its tests
validate fixtures, reviewer ops and gold annotations against the schemas written here, and
``tests/test_review_contracts.py`` fails while the committed files differ from these models, so
neither side can drift silently.

Writes ``lib/review/schema/``:

- ``review_bundle.json``, ``review_package.json``, ``review_result.json``: the review exchange (§11.5)
- ``sidecar.json``: what ``binding.sidecar()`` emits (probabilities and evidence by FnaDocument path)
- ``op_payloads.json``: the reducer's payload models, one ``$defs`` entry per op (``ADD`` …)
- ``gold_annotation.json``: the annotate-mode export (§15.2)
- ``slots.json``: the slot catalog fields the overlay needs (labels, units, categories, tiers)

Run from ``pipeline/``: ``.venv/bin/python scripts/export_review_schemas.py`` (``--check`` only compares).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PIPELINE = Path(__file__).resolve().parents[1]
REPO = PIPELINE.parent
OUT = REPO / "lib" / "review" / "schema"

if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from pydantic.json_schema import models_json_schema

from advisor_intel.evals.annotation import GoldAnnotation
from advisor_intel.extract.reducer import PAYLOADS
from advisor_intel.models.review import ReviewBundle, ReviewPackage, ReviewResult
from advisor_intel.models.sidecar import Sidecar
from advisor_intel.schema.slots import load_catalog

SLOT_KEYS = (
    "slot_id",
    "fna_path",
    "group",
    "cardinality",
    "entity_type",
    "policy",
    "value_type",
    "fna_unit",
    "allowed_dists",
    "categories",
    "bounds",
    "question",
    "sensitivity_tier",
    "exported",
    "store_as",
    "label",
)


def _payloads() -> dict[str, Any]:
    _, schema = models_json_schema([(model, "validation") for model in PAYLOADS.values()], title="Reducer op payloads")
    names = {op: model.__name__ for op, model in PAYLOADS.items()}
    return {**schema, "description": "Payload of each StateOp kind; validate a payload against $defs[ops[op]].", "ops": names}


def _slots() -> dict[str, Any]:
    cat = load_catalog()
    return {
        "version": cat.version,
        "groups": {
            g.name: {"name": g.name, "description": g.description, "list_entity": g.list_entity, "family": g.family}
            for g in cat.groups.values()
        },
        "slots": [{k: v for k, v in s.model_dump(mode="json").items() if k in SLOT_KEYS} for s in cat.slots],
    }


def documents() -> dict[str, Any]:
    return {
        "review_bundle.json": ReviewBundle.model_json_schema(),
        "review_package.json": ReviewPackage.model_json_schema(),
        "review_result.json": ReviewResult.model_json_schema(),
        "sidecar.json": Sidecar.model_json_schema(),
        "op_payloads.json": _payloads(),
        "gold_annotation.json": GoldAnnotation.model_json_schema(),
        "slots.json": _slots(),
    }


def render(doc: Any) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    OUT.mkdir(parents=True, exist_ok=True)
    for name, doc in documents().items():
        path = OUT / name
        text = render(doc)
        if path.exists() and path.read_text() == text:
            continue
        stale.append(name)
        if not check:
            path.write_text(text)
    if check and stale:
        print(f"stale: {', '.join(stale)} (run scripts/export_review_schemas.py)", file=sys.stderr)
        return 1
    print(("up to date" if not stale else f"wrote {', '.join(stale)}") + f" in {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
