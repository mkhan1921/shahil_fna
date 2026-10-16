"""Parity harness (§12.7): run the TypeScript engine on generated documents through Node, then check that
the Python port gives the same results (see :mod:`.compare` for the tolerances).

The Node side is ``generate.parity.ts`` (run by vitest with ``vitest.config.mts``); it writes
``parity.jsonl`` (one document with its TypeScript analysis and draft recommendations per line) and
``meta.json`` (the catalogs, risk questions, default assumptions and tax tables) to the output directory,
by default ``<repo>/fna_parity_out`` (gitignored). A subset is committed under ``pipeline/tests/fixtures``
so parity is also checked where Node is not installed.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import PIPELINE_DIR, REPO_DIR
from ..fna_engine import analyse, catalog, draft_recommendations, risk_profile
from ..fna_engine.defaults import DEFAULT_ASSUMPTIONS
from ..fna_engine.tables import TS_TABLE_FIELDS, table_for
from ..models.fna import FnaDocument
from .compare import Report, compare

DEFAULT_OUT = REPO_DIR / "fna_parity_out"
DEFAULT_N = 2000
DEFAULT_SEED = 20261016
FIXTURE = PIPELINE_DIR / "tests" / "fixtures" / "fna_parity_subset.jsonl.gz"
VITEST_CONFIG = Path(__file__).with_name("vitest.config.mts")


# --------------------------------------------------------------------------- Node side


def node_unavailable_reason(repo: Path = REPO_DIR) -> str | None:
    """Why the TypeScript side cannot run here, or None when it can."""
    if shutil.which("node") is None:
        return "Node.js is not installed"
    if not (repo / "node_modules" / ".bin" / "vitest").exists():
        return f"the app's node_modules are missing ({repo / 'node_modules'}); run npm install at the repo root"
    return None


def generate(out: Path = DEFAULT_OUT, *, n: int = DEFAULT_N, seed: int = DEFAULT_SEED, repo: Path = REPO_DIR) -> float:
    """Run the TypeScript generator; returns the seconds it took."""
    reason = node_unavailable_reason(repo)
    if reason:
        raise RuntimeError(f"cannot run the TypeScript engine: {reason}")
    out.mkdir(parents=True, exist_ok=True)
    env = os.environ | {"TZ": "UTC", "FNA_PARITY_N": str(n), "FNA_PARITY_SEED": str(seed), "FNA_PARITY_OUT": str(out)}
    cmd = [str(repo / "node_modules" / ".bin" / "vitest"), "run", "--config", str(VITEST_CONFIG)]
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"the TypeScript generator failed:\n{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}")
    return time.perf_counter() - start


# --------------------------------------------------------------------------- reading results


def read_records(path: Path) -> Iterator[dict[str, Any]]:
    """Records from ``parity.jsonl`` (or a gzipped subset whose first line is the meta record)."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def parse_now(s: str) -> datetime:
    return datetime.fromisoformat(s)  # "...Z" from Date.toISOString()


# --------------------------------------------------------------------------- checks


def check_record(rec: dict[str, Any]) -> Report:
    """The Python analysis and draft recommendations of one record against the TypeScript ones."""
    doc = FnaDocument.model_validate(rec["doc"])
    now = parse_now(rec["now"])
    analysis = analyse(doc, today=now)
    report = compare(rec["analysis"], analysis.model_dump(mode="json"), path=f"[{rec['i']}].analysis")
    drafts = [r.model_dump(mode="json") for r in draft_recommendations(doc, analysis, today=now)]
    compare(rec["recommendations"], drafts, ignore=frozenset({"id"}), path=f"[{rec['i']}].recommendations", report=report)
    return report


def check_meta(meta: dict[str, Any]) -> list[str]:
    """Catalogs, risk questions, default assumptions and tax tables: the Python copies against the TypeScript."""
    r = compare(meta["catalogs"], json.loads(json.dumps(catalog.as_json())), path="catalogs")
    compare(meta["risk"], risk_profile.as_json(), path="risk", report=r)
    compare(meta["defaultAssumptions"], DEFAULT_ASSUMPTIONS.model_dump(mode="json"), path="defaultAssumptions", report=r)
    for year, ts_table in meta["taxTables"].items():
        py = table_for(year).model_dump(mode="json", include=set(TS_TABLE_FIELDS))
        compare(ts_table, py, path=f"taxTables[{year}]", report=r)
    for year, ts_file in meta.get("taxTableFiles", {}).items():
        py = table_for(year).model_dump(mode="json")
        compare({k: v for k, v in ts_file.items() if k != "$schema"}, py, path=f"taxTableFiles[{year}]", report=r)
    return r.diffs


@dataclass
class Summary:
    """The outcome of checking many records."""

    records: int = 0
    failed: list[int] = field(default_factory=list)
    diffs: list[str] = field(default_factory=list)
    numbers: int = 0
    max_money: float = 0.0
    max_ratio: float = 0.0
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failed and not self.diffs

    def describe(self) -> str:
        head = (
            f"{self.records} documents, {self.numbers:,} numbers compared in {self.seconds:.1f}s;"
            f" largest gaps: R{self.max_money:.2g} on money,"
            f" {self.max_ratio:.2g} on ratios"
        )
        if self.ok:
            return f"parity OK: {head}"
        return f"parity FAILED on {len(self.failed)} documents ({head}):\n" + "\n".join(self.diffs[:40])


def check_records(records: Iterable[dict[str, Any]]) -> Summary:
    s = Summary()
    start = time.perf_counter()
    for rec in records:
        if "meta" in rec:
            s.diffs.extend(check_meta(rec["meta"]))
            continue
        s.records += 1
        r = check_record(rec)
        s.numbers += r.numbers
        s.max_money = max(s.max_money, r.max_money)
        s.max_ratio = max(s.max_ratio, r.max_ratio)
        if not r.ok:
            s.failed.append(rec["i"])
            s.diffs.extend(r.diffs[:10])
    s.seconds = time.perf_counter() - start
    return s


def check(out: Path = DEFAULT_OUT) -> Summary:
    """Check every record the generator wrote to ``out``, and the catalogs in its meta.json."""
    meta = json.loads((out / "meta.json").read_text())
    return check_records([{"meta": meta}, *read_records(out / "parity.jsonl")])


# --------------------------------------------------------------------------- committed subset


def _select(records: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """The fixed documents first (sample, empty), then greedily whichever adds the most unseen tags."""
    chosen = [r for r in records if r["i"] < 3]
    seen = {t for r in chosen for t in r["tags"]}
    rest = [r for r in records if r["i"] >= 3]
    while len(chosen) < count and rest:
        best = max(rest, key=lambda r: (len(set(r["tags"]) - seen), -r["i"]))
        chosen.append(best)
        seen |= set(best["tags"])
        rest.remove(best)
    return sorted(chosen, key=lambda r: r["i"])


def write_fixture(out: Path = DEFAULT_OUT, path: Path = FIXTURE, *, count: int = 25) -> list[int]:
    """Write ``count`` diverse records (and the meta record) to the committed gzipped fixture; returns their indices."""
    meta = json.loads((out / "meta.json").read_text())
    chosen = _select(list(read_records(out / "parity.jsonl")), count)
    lines = [json.dumps({"meta": meta}, ensure_ascii=False)] + [json.dumps(rec, ensure_ascii=False) for rec in chosen]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as fh:
        fh.write(("\n".join(lines) + "\n").encode("utf-8"))  # no timestamp, so an unchanged subset is byte-identical
    return [r["i"] for r in chosen]
