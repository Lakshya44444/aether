import asyncio
import hashlib
import json
import os
import aiosqlite
from typing import Optional, List
from src.models.schemas import DecisionTrace, DashboardStats
from src.config import config

_GENESIS = "0" * 64


class AuditLogger:
    """Decision Trace + SQLite audit log.

    Rows are hash-chained: each row stores the hash of the previous row together with
    its own content. SQLite cannot prevent an UPDATE or DELETE, so tamper-evidence is
    provided instead of a claim of immutability the storage layer cannot keep.

    Everything that can change the published metrics is inside the chain, including
    human review verdicts. A review is an appended row, not an update to the row it
    reviews, because a mutable column is outside the hash and can be forged silently.
    """

    def __init__(self):
        self.db_path = config.audit_db_path
        self._chain_lock = asyncio.Lock()

    async def init_db(self):
        """Create the parent directory and SQLite tables if they do not exist."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS decision_traces (
                    trace_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    use_case TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    trace_json TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'trace',
                    prev_hash TEXT NOT NULL DEFAULT '',
                    row_hash TEXT NOT NULL DEFAULT ''
                );
            ''')
            # Databases written before review rows existed are missing `kind`.
            try:
                await db.execute(
                    "ALTER TABLE decision_traces ADD COLUMN kind TEXT NOT NULL DEFAULT 'trace'"
                )
            except Exception:
                pass
            # The head anchors the chain's length and final hash. Without it, deleting
            # the newest rows leaves a chain that still verifies from genesis, so an
            # operator could drop every trace since the incident and pass an audit.
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

    @staticmethod
    def _hash_row(prev_hash: str, trace_json: str) -> str:
        return hashlib.sha256(f"{prev_hash}{trace_json}".encode("utf-8")).hexdigest()

    async def _head(self, db) -> tuple:
        async with db.execute('SELECT row_hash, row_count FROM audit_head WHERE id = 1') as cursor:
            row = await cursor.fetchone()
        return (row[0], row[1]) if row else (_GENESIS, 0)

    async def _append(self, db, *, trace_id, timestamp, session_id, use_case,
                      decision, payload_json, kind):
        """Appends one chained row and moves the head pointer with it."""
        prev_hash, count = await self._head(db)
        row_hash = self._hash_row(prev_hash, payload_json)
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

    async def verify_chain(self):
        """Recomputes the chain and reports the first row that does not match.

        Returns (ok, checked, first_bad_trace_id).
        """
        # ponytail: the head lives in the same file it protects, so this detects
        # accidental corruption and unsophisticated tampering, not an attacker with
        # write access to the whole database. Anchoring the head externally -- a signed
        # checkpoint, or an append to write-once storage -- is the upgrade path.
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT trace_id, trace_json, prev_hash, row_hash '
                'FROM decision_traces ORDER BY rowid ASC'
            ) as cursor:
                rows = await cursor.fetchall()
            head_hash, head_count = await self._head(db)

        expected_prev = _GENESIS
        for trace_id, trace_json, prev_hash, row_hash in rows:
            if prev_hash != expected_prev:
                return False, len(rows), trace_id
            if self._hash_row(prev_hash, trace_json) != row_hash:
                return False, len(rows), trace_id
            expected_prev = row_hash

        if head_count != len(rows) or (rows and expected_prev != head_hash):
            return False, len(rows), rows[-1][0] if rows else None
        return True, len(rows), None

    async def log_trace(self, trace: DecisionTrace):
        """Appends a tamper-evident row linked to the one before it."""
        trace_json = trace.model_dump_json()
        # Serialised so two concurrent requests cannot read the same tail hash and
        # write two rows claiming the same predecessor.
        async with self._chain_lock:
            async with aiosqlite.connect(self.db_path) as db:
                await self._append(
                    db,
                    trace_id=trace.trace_id,
                    timestamp=trace.timestamp.isoformat(),
                    session_id=trace.session_id,
                    use_case=trace.use_case.value,
                    decision=trace.decision.value,
                    payload_json=trace_json,
                    kind='trace',
                )

    async def get_trace(self, trace_id: str) -> Optional[DecisionTrace]:
        """Get specific trace."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT trace_json FROM decision_traces WHERE trace_id = ? AND kind = 'trace'",
                (trace_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return DecisionTrace.model_validate_json(row[0])
        return None

    async def get_recent_traces(self, limit=50) -> List[DecisionTrace]:
        """Get recent traces."""
        traces = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT trace_json FROM decision_traces WHERE kind = 'trace' "
                "ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ) as cursor:
                async for row in cursor:
                    traces.append(DecisionTrace.model_validate_json(row[0]))
        return traces

    async def log_human_review(self, trace_id: str, approved: bool, reviewer_id: str, reason: str,
                               timestamp: str = ""):
        """Appends the verdict as its own chained row.

        The verdict used to be an UPDATE to mutable columns on the reviewed row. Those
        columns sat outside the hash, so forging an approval left the chain intact while
        moving the false-positive count the dashboard publishes. They are gone; a review
        is now an append like any other event, and forging one breaks the chain.
        """
        from datetime import datetime, timezone
        stamp = timestamp or datetime.now(timezone.utc).isoformat()
        payload = json.dumps({
            "trace_id": trace_id,
            "approved": approved,
            "reviewer_id": reviewer_id,
            "reason": reason,
            "timestamp": stamp,
        }, sort_keys=True)

        async with self._chain_lock:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT session_id, use_case FROM decision_traces "
                    "WHERE trace_id = ? AND kind = 'trace'",
                    (trace_id,),
                ) as cursor:
                    parent = await cursor.fetchone()
                session_id, use_case = parent if parent else ("", "")

                async with db.execute(
                    "SELECT COUNT(*) FROM decision_traces WHERE kind = 'review' "
                    "AND json_extract(trace_json, '$.trace_id') = ?",
                    (trace_id,),
                ) as cursor:
                    seq = (await cursor.fetchone())[0]

                await self._append(
                    db,
                    trace_id=f"review:{trace_id}:{seq}",
                    timestamp=stamp,
                    session_id=session_id,
                    use_case=use_case,
                    decision="REVIEW",
                    payload_json=payload,
                    kind='review',
                )

    async def get_review(self, trace_id: str) -> Optional[dict]:
        """Returns the latest chained review verdict for a trace, if any."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT trace_json FROM decision_traces WHERE kind = 'review' "
                "AND json_extract(trace_json, '$.trace_id') = ? ORDER BY rowid DESC LIMIT 1",
                (trace_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return json.loads(row[0]) if row else None

    async def get_stats(self) -> DashboardStats:
        """Aggregates dashboard statistics from reviewed and unreviewed traces."""
        stats = DashboardStats()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT COUNT(*), AVG(json_extract(trace_json, "$.total_latency_ms")) '
                "FROM decision_traces WHERE kind = 'trace'"
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    stats.total_evaluations = row[0]
                    stats.avg_latency_ms = row[1] or 0.0

            async with db.execute(
                "SELECT decision, COUNT(*) FROM decision_traces WHERE kind = 'trace' "
                "GROUP BY decision"
            ) as cursor:
                async for row in cursor:
                    stats.decisions[row[0]] = row[1]

            # A reviewer approving something Aether stopped means Aether was wrong
            # to stop it: a false positive. A reviewer rejecting something Aether let
            # through means it should have been caught: a false negative. Anything else
            # is a confirmed decision.
            #
            # The verdict is read out of the review row's hashed payload, not out of the
            # mutable review columns, so forging an approval to move these numbers breaks
            # the chain that /api/audit/verify checks.
            stopped = ("BLOCK", "ESCALATE", "REDACT", "WARN")
            confirmed_alerts = 0
            reviewed_alerts = 0
            async with db.execute('''
                SELECT t.decision, json_extract(r.trace_json, '$.approved'), COUNT(*)
                FROM decision_traces t
                JOIN (
                    SELECT trace_json,
                           json_extract(trace_json, '$.trace_id') AS tid,
                           ROW_NUMBER() OVER (
                               PARTITION BY json_extract(trace_json, '$.trace_id')
                               ORDER BY rowid DESC
                           ) AS rn
                    FROM decision_traces WHERE kind = 'review'
                ) r ON r.tid = t.trace_id AND r.rn = 1
                WHERE t.kind = 'trace'
                GROUP BY t.decision, json_extract(r.trace_json, '$.approved')
            ''') as cursor:
                async for decision, approved, count in cursor:
                    was_alert = decision in stopped
                    if was_alert:
                        reviewed_alerts += count
                    if approved:
                        if was_alert:
                            stats.false_positive_count += count
                        # An allowed response the reviewer also approves is simply correct.
                    else:
                        if was_alert:
                            confirmed_alerts += count
                        else:
                            stats.false_negative_count += count

            stats.recent_traces = await self.get_recent_traces(10)

            # Alert-to-incident conversion is the share of raised alerts a human
            # confirms as genuine — not the share of traffic that raised an alert,
            # which is the alert rate and a different number entirely.
            if reviewed_alerts:
                stats.alert_to_incident_rate = confirmed_alerts / reviewed_alerts

            for trace in stats.recent_traces:
                for result in trace.detection_results:
                    if result.flagged:
                        key = result.category.value
                        stats.risk_distribution[key] = stats.risk_distribution.get(key, 0) + 1

        return stats
