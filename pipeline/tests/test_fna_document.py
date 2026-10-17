"""The Python FnaDocument mirror must round-trip the app's own documents unchanged."""

from __future__ import annotations

from datetime import date
from typing import Any

from advisor_intel.models.fna import ExportFile, FnaDocument

VOLATILE = {"id", "createdAt", "updatedAt", "exportedAt", "meetingDate"}  # meetingDate: "today" on both sides, frozen in the fixture


def _strip(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def test_sample_document_round_trips(sample_fna_json):
    doc = FnaDocument.model_validate(sample_fna_json)
    assert doc.model_dump(mode="json") == sample_fna_json
    assert doc.client.firstName == "Thabo"
    assert doc.schemaVersion == 2


def test_defaults_match_new_document(empty_fna_json):
    """Every default in the mirror equals lib/fna/defaults.ts newDocument()."""
    ours = _strip(FnaDocument().model_dump(mode="json"))
    theirs = _strip(empty_fna_json)
    # Both sides default the meeting date to the day the document is made: the fixture carries the day it was
    # exported, so only the form is compared.
    date.fromisoformat(ours["engagement"].pop("meetingDate"))
    date.fromisoformat(theirs["engagement"].pop("meetingDate"))
    assert ours == theirs


def test_list_row_defaults_match(sample_fna_json):
    from advisor_intel.models.fna import LIST_FIELDS

    for key, model in LIST_FIELDS.items():
        rows = sample_fna_json
        for part in key.split("."):
            rows = rows[part]
        for row in rows:
            parsed = model.model_validate(row)
            assert parsed.model_dump(mode="json") == row, key


def test_export_file_shape(sample_fna_json):
    f = ExportFile(document=FnaDocument.model_validate(sample_fna_json))
    data = f.model_dump(mode="json")
    assert data["format"] == "shahil-fna"
    assert data["version"] == 1
    assert data["encrypted"] is False
    assert data["document"]["schemaVersion"] == 2


def test_unknown_fields_survive(sample_fna_json):
    sample_fna_json["futureField"] = {"x": 1}
    sample_fna_json["client"]["futurePersonField"] = "kept"
    doc = FnaDocument.model_validate(sample_fna_json)
    out = doc.model_dump(mode="json")
    assert out["futureField"] == {"x": 1}
    assert out["client"]["futurePersonField"] == "kept"
