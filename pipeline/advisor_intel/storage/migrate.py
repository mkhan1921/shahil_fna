"""A tiny migration runner: ordered ``migrations/NNNN_name.sql`` files, applied once each (§14.2).

- Each migration runs in its own transaction and is recorded in ``schema_migrations`` with a
  checksum; editing an applied migration is an error, so the history stays replayable.
- A session advisory lock serialises concurrent runners (several workers starting at once).
- ``schema.sql`` is the full schema as one readable file, rendered from the migrations
  (``python -m advisor_intel.storage.migrate render > schema.sql``); a test keeps them in step.

Usage: ``python -m advisor_intel.storage.migrate [up|status|render] [--dsn DSN]``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
SCHEMA_FILE = Path(__file__).parent / "schema.sql"
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
_LOCK_KEY = 7_311_942_001  # arbitrary, constant: pg_advisory_lock key for this runner

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    integer PRIMARY KEY,
  name       text NOT NULL,
  checksum   text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


@dataclass(frozen=True)
class MigrationStatus:
    version: int
    name: str
    applied_at: datetime | None


class MigrationError(RuntimeError):
    pass


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Every migration file, in version order. Versions must be unique and contiguous from 1."""
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        m = _NAME.match(path.name)
        if not m:
            raise MigrationError(f"bad migration file name {path.name}: expected NNNN_name.sql")
        found.append(Migration(version=int(m.group(1)), name=m.group(2), sql=path.read_text()))
    versions = [m.version for m in found]
    if versions != list(range(1, len(found) + 1)):
        raise MigrationError(f"migration versions must run 1..n without gaps, got {versions}")
    return found


def render_schema(directory: Path = MIGRATIONS_DIR) -> str:
    """The full schema as one file: the migrations in order, each under a header."""
    parts = [
        (
            "-- Advisor Call Intelligence: full Postgres schema (spec §14.2).\n"
            "-- Rendered from storage/migrations by `python -m advisor_intel.storage.migrate render`; do not edit by hand.\n"
        )
    ]
    for m in discover(directory):
        parts.append(f"\n-- ===== {m.version:04d}_{m.name} =====\n\n{m.sql.strip()}\n")
    return "".join(parts)


async def _applied(conn: psycopg.AsyncConnection) -> dict[int, dict]:
    cur = await conn.execute("SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version")
    return {r["version"]: r for r in await cur.fetchall()}


async def migrate(dsn: str, *, target: int | None = None, directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending migrations up to ``target`` (default: all). Returns the versions applied."""
    migrations = discover(directory)
    applied_now: list[int] = []
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        await conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            await conn.execute(_CREATE_TABLE)
            done = await _applied(conn)
            for m in migrations:
                if target is not None and m.version > target:
                    break
                if m.version in done:
                    if done[m.version]["checksum"] != m.checksum:
                        raise MigrationError(
                            f"migration {m.version:04d}_{m.name} changed after it was applied; add a new migration instead"
                        )
                    continue
                async with conn.transaction():
                    await conn.execute(m.sql)  # no parameters: several statements run in one call
                    await conn.execute(
                        "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                        (m.version, m.name, m.checksum),
                    )
                applied_now.append(m.version)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
    return applied_now


async def status(dsn: str, *, directory: Path = MIGRATIONS_DIR) -> list[MigrationStatus]:
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        await conn.execute(_CREATE_TABLE)
        done = await _applied(conn)
    return [MigrationStatus(m.version, m.name, done[m.version]["applied_at"] if m.version in done else None) for m in discover(directory)]


def main(argv: list[str] | None = None) -> int:
    from ..config import get_settings

    p = argparse.ArgumentParser(prog="advisor_intel.storage.migrate", description="Apply or inspect database migrations.")
    p.add_argument("command", nargs="?", choices=("up", "status", "render"), default="up")
    p.add_argument("--dsn", default=None, help="Postgres DSN (default: ADVISOR_INTEL_DATABASE_URL)")
    p.add_argument("--target", type=int, default=None)
    a = p.parse_args(argv)
    if a.command == "render":
        sys.stdout.write(render_schema())
        return 0
    dsn = a.dsn or get_settings().database_url
    if a.command == "status":
        for s in asyncio.run(status(dsn)):
            print(f"{s.version:04d}_{s.name}: {s.applied_at.isoformat() if s.applied_at else 'pending'}")
        return 0
    applied = asyncio.run(migrate(dsn, target=a.target))
    print(f"applied {len(applied)} migration(s): {applied}" if applied else "database is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
