"""The practice glossary in Postgres: adviser-confirmed term meanings (spec §8.5, §14.2).

Implements the lexicon's ``GlossaryStore`` protocol (``get``, ``entries``, ``put`` over
``lexicon.glossary.GlossaryEntry``) on the ``glossary`` table. ``SyncGlossaryStore`` is the protocol
itself, for ``lookup_term``, which runs in a worker thread; ``PostgresGlossaryStore`` has the same
methods, async, for the API and activities. Terms and aliases are keyed by the lexicon's canonical
form (``canon``), as in ``InMemoryGlossary``. ``glossary_lines`` renders the confirmed terms for the
extraction context pack and the agent's inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..lexicon.glossary import GlossaryEntry
from ..lexicon.phrases import canon
from .db import Database

__all__ = ["GlossaryEntry", "PostgresGlossaryStore", "SyncGlossaryStore", "glossary_lines", "normalise_term"]


def normalise_term(term: str) -> str:
    """The glossary key of a term or alias (the lexicon's canonical form)."""
    return canon(term)


def _row_to_entry(r: dict[str, Any]) -> GlossaryEntry:
    return GlossaryEntry(
        term=r["display_term"],
        aliases=list(r["aliases"] or []),
        canonical_name=r["canonical_name"],
        product_type=r["product_type"] or None,
        provider=r["provider"] or None,
        kind=r["kind"] or None,
        source=r["source"],
        source_url=r["source_url"] or None,
        confirmed_by=r["confirmed_by"] or "",
        confirmed_at=r["confirmed_at"] or _EPOCH,
    )


_EPOCH = datetime.fromtimestamp(0, UTC)  # a row written without a confirmation time
_GET = "SELECT * FROM glossary WHERE term = %(k)s OR %(k)s = ANY(aliases) ORDER BY (term = %(k)s) DESC LIMIT 1"
_PUT = """
INSERT INTO glossary (term, display_term, aliases, canonical_name, product_type, provider, kind, source, source_url,
                      confirmed_by, confirmed_at)
VALUES (%(term)s, %(display)s, %(aliases)s, %(canonical)s, %(product_type)s, %(provider)s, %(kind)s, %(source)s, %(source_url)s,
        %(by)s, %(at)s)
ON CONFLICT (term) DO UPDATE SET
  display_term = EXCLUDED.display_term,
  aliases = ARRAY(SELECT DISTINCT unnest(glossary.aliases || EXCLUDED.aliases)),
  canonical_name = EXCLUDED.canonical_name, product_type = EXCLUDED.product_type, provider = EXCLUDED.provider,
  kind = EXCLUDED.kind, source = EXCLUDED.source, source_url = EXCLUDED.source_url,
  confirmed_by = EXCLUDED.confirmed_by, confirmed_at = EXCLUDED.confirmed_at
"""
_ALL = "SELECT * FROM glossary ORDER BY term"


def _put_params(entry: GlossaryEntry) -> dict[str, Any]:
    key = normalise_term(entry.term)
    if not key:
        raise ValueError("glossary term is empty")
    aliases = sorted({normalise_term(a) for a in entry.aliases if normalise_term(a)} - {key})
    return {
        "term": key,
        "display": entry.term.strip(),
        "aliases": aliases,
        "canonical": entry.canonical_name,
        "product_type": entry.product_type or "",
        "provider": entry.provider or "",
        "kind": entry.kind or "",
        "source": entry.source,
        "source_url": entry.source_url or "",
        "by": entry.confirmed_by or None,
        "at": entry.confirmed_at or datetime.now(UTC),
    }


def glossary_lines(entries: list[GlossaryEntry]) -> list[str]:
    """One line per confirmed term for the context pack (§7.2, §8.5): ``term = canonical name (type, provider)``."""
    lines = []
    for e in sorted(entries, key=lambda x: normalise_term(x.term)):
        detail = ", ".join(x for x in (e.product_type, e.provider) if x)
        same = normalise_term(e.term) == normalise_term(e.canonical_name)
        lines.append((e.canonical_name if same else f"{e.term} = {e.canonical_name}") + (f" ({detail})" if detail else ""))
    return lines


class PostgresGlossaryStore:
    """The glossary for async callers, over the shared connection pool."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, term: str) -> GlossaryEntry | None:
        async with self.db.connection() as conn:
            cur = await conn.execute(_GET, {"k": normalise_term(term)})
            row = await cur.fetchone()
        return _row_to_entry(row) if row else None

    async def entries(self) -> list[GlossaryEntry]:
        async with self.db.connection() as conn:
            cur = await conn.execute(_ALL)
            return [_row_to_entry(r) for r in await cur.fetchall()]

    async def put(self, entry: GlossaryEntry) -> None:
        async with self.db.connection() as conn:
            await conn.execute(_PUT, _put_params(entry))


class SyncGlossaryStore:
    """The lexicon's ``GlossaryStore`` over blocking connections (one per call), for ``lookup_term``'s thread."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def get(self, term: str) -> GlossaryEntry | None:
        with self._conn() as conn:
            row = conn.execute(_GET, {"k": normalise_term(term)}).fetchone()
        return _row_to_entry(row) if row else None

    def entries(self) -> list[GlossaryEntry]:
        with self._conn() as conn:
            return [_row_to_entry(r) for r in conn.execute(_ALL).fetchall()]

    def put(self, entry: GlossaryEntry) -> None:
        with self._conn() as conn:
            conn.execute(_PUT, _put_params(entry))
