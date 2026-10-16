"""The practice glossary in Postgres: adviser-confirmed term meanings (spec §8.5, §14.2).

Implements the lexicon's ``GlossaryStore`` protocol with the method names ``get``, ``put`` and
``search``. ``PostgresGlossaryStore`` is async (activities, the API, the agent's ``lookup_term``
tool); ``SyncGlossaryStore`` gives the same methods over a blocking connection for code that is
not async. Terms and aliases are matched after normalisation (case, spacing, punctuation).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel

from .db import Database

_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s&+-]")


def normalise_term(term: str) -> str:
    return _SPACE.sub(" ", _PUNCT.sub(" ", term.lower())).strip()


class GlossaryEntry(BaseModel):
    term: str
    canonical_name: str
    aliases: list[str] = []
    product_type: str = ""
    provider: str = ""
    source: str = "adviser"  # adviser | catalogue | web | crm; web answers enter only once confirmed
    source_url: str = ""
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None

    @property
    def key(self) -> str:
        return normalise_term(self.term)


def _row_to_entry(r: dict[str, Any]) -> GlossaryEntry:
    return GlossaryEntry(
        term=r["display_term"],
        canonical_name=r["canonical_name"],
        aliases=list(r["aliases"] or []),
        product_type=r["product_type"],
        provider=r["provider"],
        source=r["source"],
        source_url=r["source_url"],
        confirmed_by=r["confirmed_by"],
        confirmed_at=r["confirmed_at"],
    )


_GET = "SELECT * FROM glossary WHERE term = %(k)s OR %(k)s = ANY(aliases) ORDER BY (term = %(k)s) DESC LIMIT 1"
_PUT = """
INSERT INTO glossary (term, display_term, aliases, canonical_name, product_type, provider, source, source_url, confirmed_by, confirmed_at)
VALUES (%(term)s, %(display)s, %(aliases)s, %(canonical)s, %(product_type)s, %(provider)s, %(source)s, %(source_url)s, %(by)s, %(at)s)
ON CONFLICT (term) DO UPDATE SET
  display_term = EXCLUDED.display_term,
  aliases = ARRAY(SELECT DISTINCT unnest(glossary.aliases || EXCLUDED.aliases)),
  canonical_name = EXCLUDED.canonical_name, product_type = EXCLUDED.product_type, provider = EXCLUDED.provider,
  source = EXCLUDED.source, source_url = EXCLUDED.source_url,
  confirmed_by = EXCLUDED.confirmed_by, confirmed_at = EXCLUDED.confirmed_at
"""
_SEARCH = """
SELECT * FROM glossary
WHERE term LIKE %(like)s OR canonical_name ILIKE %(like)s OR provider ILIKE %(like)s
   OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE a LIKE %(like)s)
LIMIT %(cap)s
"""
_ALL = "SELECT * FROM glossary ORDER BY term LIMIT %(cap)s"


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
        "product_type": entry.product_type,
        "provider": entry.provider,
        "source": entry.source,
        "source_url": entry.source_url,
        "by": entry.confirmed_by,
        "at": entry.confirmed_at or datetime.now(timezone.utc),
    }


def _rank(query: str, entries: list[GlossaryEntry], limit: int) -> list[GlossaryEntry]:
    from rapidfuzz import fuzz

    q = normalise_term(query)

    def score(e: GlossaryEntry) -> float:
        names = [normalise_term(e.term), normalise_term(e.canonical_name), *e.aliases]
        return max(fuzz.WRatio(q, n) for n in names if n)

    return sorted(entries, key=lambda e: (-score(e), e.key))[:limit]


class PostgresGlossaryStore:
    """Async glossary over the shared connection pool."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, term: str) -> GlossaryEntry | None:
        async with self.db.connection() as conn:
            cur = await conn.execute(_GET, {"k": normalise_term(term)})
            row = await cur.fetchone()
        return _row_to_entry(row) if row else None

    async def put(self, entry: GlossaryEntry) -> None:
        async with self.db.connection() as conn:
            await conn.execute(_PUT, _put_params(entry))

    async def search(self, query: str, limit: int = 10) -> list[GlossaryEntry]:
        """Entries matching any word of ``query``, best fuzzy match first; the whole glossary when nothing matches."""
        words = [w for w in normalise_term(query).split() if len(w) >= 2] or [normalise_term(query)]
        found: dict[str, GlossaryEntry] = {}
        async with self.db.connection() as conn:
            for w in words:
                cur = await conn.execute(_SEARCH, {"like": f"%{w}%", "cap": 200})
                for r in await cur.fetchall():
                    found[r["term"]] = _row_to_entry(r)
            if not found:
                cur = await conn.execute(_ALL, {"cap": 500})
                found = {r["term"]: _row_to_entry(r) for r in await cur.fetchall()}
        return _rank(query, list(found.values()), limit)

    async def all(self) -> list[GlossaryEntry]:
        async with self.db.connection() as conn:
            cur = await conn.execute(_ALL, {"cap": 100_000})
            return [_row_to_entry(r) for r in await cur.fetchall()]


class SyncGlossaryStore:
    """The same glossary for blocking callers (one connection per call)."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def get(self, term: str) -> GlossaryEntry | None:
        with self._conn() as conn:
            row = conn.execute(_GET, {"k": normalise_term(term)}).fetchone()
        return _row_to_entry(row) if row else None

    def put(self, entry: GlossaryEntry) -> None:
        with self._conn() as conn:
            conn.execute(_PUT, _put_params(entry))

    def search(self, query: str, limit: int = 10) -> list[GlossaryEntry]:
        words = [w for w in normalise_term(query).split() if len(w) >= 2] or [normalise_term(query)]
        found: dict[str, GlossaryEntry] = {}
        with self._conn() as conn:
            for w in words:
                for r in conn.execute(_SEARCH, {"like": f"%{w}%", "cap": 200}).fetchall():
                    found[r["term"]] = _row_to_entry(r)
            if not found:
                found = {r["term"]: _row_to_entry(r) for r in conn.execute(_ALL, {"cap": 500}).fetchall()}
        return _rank(query, list(found.values()), limit)
