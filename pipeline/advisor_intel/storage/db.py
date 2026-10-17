"""A small async connection pool over psycopg 3 (spec §14.2: Postgres holds the data).

Connections run in autocommit mode; ``transaction()`` opens an explicit transaction block.
The pool is bound to the event loop that first used it: a different loop (a new test, a new
worker thread loop) gets fresh connections, because an async connection cannot cross loops.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

Connection = psycopg.AsyncConnection[dict[str, Any]]


class Database:
    """Hands out pooled connections to one Postgres database."""

    def __init__(self, dsn: str, *, max_size: int = 10, connect_timeout: float = 10.0) -> None:
        self.dsn = dsn
        self.max_size = max_size
        self.connect_timeout = connect_timeout
        self._idle: list[Connection] = []
        self._in_use = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cond: asyncio.Condition | None = None

    def _bind_loop(self) -> asyncio.Condition:
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._cond is None:
            # Connections opened on another loop cannot be used (or awaited) here: drop them.
            self._idle = []
            self._in_use = 0
            self._loop = loop
            self._cond = asyncio.Condition()
        return self._cond

    async def _open(self) -> Connection:
        return await psycopg.AsyncConnection.connect(
            self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=int(self.connect_timeout)
        )

    @contextlib.asynccontextmanager
    async def connection(self) -> AsyncIterator[Connection]:
        cond = self._bind_loop()
        async with cond:
            while not self._idle and self._in_use >= self.max_size:
                await cond.wait()
            conn = self._idle.pop() if self._idle else None
            self._in_use += 1
        try:
            if conn is None or conn.closed or conn.broken:
                conn = await self._open()
            yield conn
        except BaseException:
            if conn is not None and not conn.closed and conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                with contextlib.suppress(Exception):
                    await conn.rollback()
            raise
        finally:
            async with cond:
                self._in_use -= 1
                if (
                    conn is not None
                    and not conn.closed
                    and not conn.broken
                    and conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
                ):
                    self._idle.append(conn)
                cond.notify()

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[Connection]:
        async with self.connection() as conn, conn.transaction():
            yield conn

    async def close(self) -> None:
        idle, self._idle = self._idle, []
        for conn in idle:
            with contextlib.suppress(Exception):
                await conn.close()

    async def ping(self) -> bool:
        try:
            async with self.connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except psycopg.Error as e:
            log.warning("database ping failed: %s", e)
            return False
