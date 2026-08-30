"""Storage for the hash-chained audit log.

Two backends behind one interface, chosen by `AETHER_AUDIT_DSN`:

- **SQLite** (default) — a local file. Correct for a single-process appliance and
  nothing else: appends are serialised by an `asyncio.Lock` that exists only inside
  one process, so a second worker can read the same tail hash and write two rows
  claiming the same predecessor. The chain forks and `verify_chain` starts failing for
  a reason that is not tampering.
- **Postgres** — takes `SELECT ... FOR UPDATE` on the head row inside the append
  transaction, which is a lock every worker in the deployment respects. This is what
  makes running more than one worker safe.

The hashing itself is not in here. The backend hands the caller the previous hash
inside the lock and writes back whatever it returns, so there is exactly one
implementation of the chain rule regardless of where the rows live.

Dialect differences that actually matter, kept in one place rather than sprinkled
through the queries: parameter style (`?` vs `$1`), JSON extraction
(`json_extract(col, '$.k')` vs `col::jsonb ->> 'k'`), and the insertion-order column
(SQLite's implicit `rowid` vs an explicit `BIGSERIAL`).
"""
import asyncio
import os
from typing import Any, Callable, List, Optional, Sequence, Tuple


class ChainBackend:
    """Append-ordered rows with a head pointer."""

    #: Column that orders rows by insertion.
    order_by: str = "rowid"

    async def init(self) -> None: ...
    async def close(self) -> None: ...

    def json_field(self, column: str, key: str) -> str:
        """SQL fragment extracting a top-level JSON key as text."""
        raise NotImplementedError

    def json_number(self, column: str, key: str) -> str:
        """Same, cast to a number so it can be averaged.

        SQLite's json_extract already yields a number; Postgres's `->>` yields text,
        and AVG over text is an error rather than a wrong answer, which is at least
        the good kind of failure.
        """
        return self.json_field(column, key)

    async def head(self) -> Tuple[str, int]:
        raise NotImplementedError

    async def append(self, *, trace_id, timestamp, session_id, use_case, decision,
                     payload_json, kind, hash_row: Callable[[str, str], str]) -> str:
        """Appends one chained row under whatever lock this backend provides.

        `hash_row(prev_hash, payload_json)` is called inside that lock.
        """
        raise NotImplementedError

    async def fetch_all_rows(self) -> List[Tuple[str, str, str, str]]:
        raise NotImplementedError

    async def query(self, sql: str, params: Sequence[Any] = ()) -> List[tuple]:
        raise NotImplementedError


_GENESIS = "0" * 64

_CREATE_TRACES_COMMON = '''
    trace_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    session_id TEXT NOT NULL,
    use_case TEXT NOT NULL,
    decision TEXT NOT NULL,
    trace_json TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'trace',
    prev_hash TEXT NOT NULL DEFAULT '',
    row_hash TEXT NOT NULL DEFAULT ''
'''


class SqliteBackend(ChainBackend):
    """A local file. Single process only — see the module docstring."""

    order_by = "rowid"

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    def json_field(self, column: str, key: str) -> str:
        return f"json_extract({column}, '$.{key}')"

    async def init(self) -> None:
        import aiosqlite
        # aiosqlite will not create a missing parent directory, so a configured path
        # like /var/lib/aether/audit.db fails at startup rather than at first write.
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f"CREATE TABLE IF NOT EXISTS decision_traces ({_CREATE_TRACES_COMMON});"
            )
            # Databases written before review rows existed are missing `kind`.
            try:
                await db.execute(
                    "ALTER TABLE decision_traces ADD COLUMN kind TEXT NOT NULL DEFAULT 'trace'"
                )
            except Exception:
                pass
            await db.execute('''
                CREATE TABLE IF NOT EXISTS audit_head (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    row_hash TEXT NOT NULL,
                    row_count INTEGER NOT NULL
                );
            ''')
            await db.execute(
                'CREATE INDEX IF NOT EXISTS idx_traces_ts ON decision_traces(timestamp DESC)'
            )
            await db.execute('CREATE INDEX IF NOT EXISTS idx_traces_kind ON decision_traces(kind)')
            await db.commit()

    async def head(self) -> Tuple[str, int]:
        import aiosqlite
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                'SELECT row_hash, row_count FROM audit_head WHERE id = 1'
            ) as cursor:
                row = await cursor.fetchone()
        return (row[0], row[1]) if row else (_GENESIS, 0)

    async def append(self, *, trace_id, timestamp, session_id, use_case, decision,
                     payload_json, kind, hash_row) -> str:
        import aiosqlite
        # Serialised so two concurrent requests cannot read the same tail hash and
        # write two rows claiming the same predecessor. Process-local: this is exactly
        # the guarantee that does not survive a second worker.
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                async with db.execute(
                    'SELECT row_hash, row_count FROM audit_head WHERE id = 1'
                ) as cursor:
                    existing = await cursor.fetchone()
                prev_hash, count = existing if existing else (_GENESIS, 0)
                row_hash = hash_row(prev_hash, payload_json)
                await db.execute('''
                    INSERT INTO decision_traces (
                        trace_id, timestamp, session_id, use_case, decision,
                        trace_json, kind, prev_hash, row_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (trace_id, timestamp, session_id, use_case, decision,
                      payload_json, kind, prev_hash, row_hash))
                await db.execute(
                    'INSERT INTO audit_head (id, row_hash, row_count) VALUES (1, ?, ?) '
                    'ON CONFLICT(id) DO UPDATE SET row_hash = excluded.row_hash, '
                    'row_count = excluded.row_count',
                    (row_hash, count + 1),
                )
                await db.commit()
        return row_hash

    async def fetch_all_rows(self):
        return await self.query(
            'SELECT trace_id, trace_json, prev_hash, row_hash '
            'FROM decision_traces ORDER BY rowid ASC'
        )

    async def query(self, sql: str, params: Sequence[Any] = ()) -> List[tuple]:
        import aiosqlite
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(sql, tuple(params)) as cursor:
                return list(await cursor.fetchall())


class PostgresBackend(ChainBackend):
    """Shared storage, and a lock every worker respects."""

    order_by = "seq"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool = None

    def json_field(self, column: str, key: str) -> str:
        return f"({column}::jsonb ->> '{key}')"

    def json_number(self, column: str, key: str) -> str:
        return f"({column}::jsonb ->> '{key}')::double precision"

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg  # imported here so asyncpg is an optional install
            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)
        return self._pool

    async def init(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(f'''
                CREATE TABLE IF NOT EXISTS decision_traces (
                    seq BIGSERIAL,
                    {_CREATE_TRACES_COMMON}
                );
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS audit_head (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    row_hash TEXT NOT NULL,
                    row_count BIGINT NOT NULL
                );
            ''')
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_traces_ts ON decision_traces(timestamp DESC)')
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_traces_kind ON decision_traces(kind)')
            # The head row must exist before it can be locked: SELECT FOR UPDATE takes
            # no lock on a row that is not there, so without this two workers would
            # both find nothing and both insert.
            await conn.execute(
                "INSERT INTO audit_head (id, row_hash, row_count) VALUES (1, $1, 0) "
                "ON CONFLICT (id) DO NOTHING", _GENESIS)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def head(self) -> Tuple[str, int]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow('SELECT row_hash, row_count FROM audit_head WHERE id = 1')
        return (row["row_hash"], row["row_count"]) if row else (_GENESIS, 0)

    async def append(self, *, trace_id, timestamp, session_id, use_case, decision,
                     payload_json, kind, hash_row) -> str:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # The cross-process lock. Every appender queues on this one row, so the
                # tail hash a worker reads is still the tail when it writes.
                row = await conn.fetchrow(
                    'SELECT row_hash, row_count FROM audit_head WHERE id = 1 FOR UPDATE')
                prev_hash, count = (row["row_hash"], row["row_count"]) if row else (_GENESIS, 0)
                row_hash = hash_row(prev_hash, payload_json)
                await conn.execute('''
                    INSERT INTO decision_traces (
                        trace_id, timestamp, session_id, use_case, decision,
                        trace_json, kind, prev_hash, row_hash
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ''', trace_id, timestamp, session_id, use_case, decision,
                     payload_json, kind, prev_hash, row_hash)
                await conn.execute(
                    'INSERT INTO audit_head (id, row_hash, row_count) VALUES (1, $1, $2) '
                    'ON CONFLICT (id) DO UPDATE SET row_hash = EXCLUDED.row_hash, '
                    'row_count = EXCLUDED.row_count',
                    row_hash, count + 1)
        return row_hash

    async def fetch_all_rows(self):
        return await self.query(
            'SELECT trace_id, trace_json, prev_hash, row_hash '
            'FROM decision_traces ORDER BY seq ASC')

    async def query(self, sql: str, params: Sequence[Any] = ()) -> List[tuple]:
        # Callers write `?`; asyncpg wants $1..$n. Rewriting here keeps every query in
        # AuditLogger in one style.
        rewritten, index = [], 0
        for char in sql:
            if char == "?":
                index += 1
                rewritten.append(f"${index}")
            else:
                rewritten.append(char)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("".join(rewritten), *params)
        return [tuple(r) for r in rows]


def open_backend(dsn: str, sqlite_path: str) -> ChainBackend:
    """Postgres when a DSN is configured, a local SQLite file otherwise."""
    if dsn:
        return PostgresBackend(dsn)
    return SqliteBackend(sqlite_path)
