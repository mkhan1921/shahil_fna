from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def sample_fna_json() -> dict:
    """The FNA app's sample household (lib/fna/sample.ts), exported by the TypeScript code."""
    return load_fixture("sample_fna.json")


@pytest.fixture
def empty_fna_json() -> dict:
    """newDocument() from lib/fna/defaults.ts."""
    return load_fixture("empty_fna.json")
