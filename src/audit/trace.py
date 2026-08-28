import asyncio
import hashlib
import json
import aiosqlite
from typing import Optional, List
from src.models.schemas import DecisionTrace, DashboardStats
from src.config import config

_GENESIS = "0" * 64


class AuditLogger:
    """Decision Trace + SQLite audit log (Section 5.8).

    Rows are hash-chained: each row stores the hash of the previous row together with
    its own content. SQLite cannot prevent an UPDATE or DELETE, so tamper-evidence is
    provided instead of a claim of immutability the storage layer cannot keep.
    """

    def __init__(self):
        self.db_path = config.audit_db_path
        self._chain_lock = asyncio.Lock()
        
    async def init_db(self):
        """Create SQLite table if not exists."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS decision_traces (
                    trace_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    use_case TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    trace_json TEXT NOT NULL,
                    human_reviewed INTEGER DEFAULT 0,
                    review_approved INTEGER DEFAULT NULL,
                    reviewer_id TEXT DEFAULT NULL,
                    review_reason TEXT DEFAULT NULL,
                    prev_hash TEXT NOT NULL DEFAULT '',
                    row_hash TEXT NOT NULL DEFAULT ''
                );
            ''')
            await db.execute(
                'CREATE INDEX IF NOT EXISTS idx_traces_ts ON decision_traces(timestamp DESC)'
            )
            await db.commit()

    @staticmethod
    def _hash_row(prev_hash: str, trace_json: str) -> str:
        return hashlib.sha256(f"{prev_hash}{trace_json}".encode("utf-8")).hexdigest()

    async def _last_hash(self, db) -> str:
        async with db.execute(
            'SELECT row_hash FROM decision_traces ORDER BY rowid DESC LIMIT 1'
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row and row[0] else _GENESIS

    async def verify_chain(self):
        """Recomputes the chain and reports the first row that does not match.

        Returns (ok, checked, first_bad_trace_id).
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT trace_id, trace_json, prev_hash, row_hash '
                'FROM decision_traces ORDER BY rowid ASC'
            ) as cursor:
                rows = await cursor.fetchall()

        expected_prev = _GENESIS
        for trace_id, trace_json, prev_hash, row_hash in rows:
            if prev_hash != expected_prev:
                return False, len(rows), trace_id
            if self._hash_row(prev_hash, trace_json) != row_hash:
                return False, len(rows), trace_id
            expected_prev = row_hash
        return True, len(rows), None

    async def log_trace(self, trace: DecisionTrace):
        """Appends a tamper-evident row linked to the one before it."""
        trace_json = trace.model_dump_json()
        # Serialised so two concurrent requests cannot read the same tail hash and
        # write two rows claiming the same predecessor.
        async with self._chain_lock:
            async with aiosqlite.connect(self.db_path) as db:
                prev_hash = await self._last_hash(db)
                row_hash = self._hash_row(prev_hash, trace_json)
                await db.execute('''
                    INSERT INTO decision_traces (
                        trace_id, timestamp, session_id, use_case, decision,
                        trace_json, prev_hash, row_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    trace.trace_id,
                    trace.timestamp.isoformat(),
                    trace.session_id,
                    trace.use_case.value,
                    trace.decision.value,
                    trace_json,
                    prev_hash,
                    row_hash,
                ))
                await db.commit()

    async def get_trace(self, trace_id: str) -> Optional[DecisionTrace]:
        """Get specific trace."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('SELECT trace_json FROM decision_traces WHERE trace_id = ?', (trace_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return DecisionTrace.model_validate_json(row[0])
        return None

    async def get_recent_traces(self, limit=50) -> List[DecisionTrace]:
        """Get recent traces."""
        traces = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('SELECT trace_json FROM decision_traces ORDER BY timestamp DESC LIMIT ?', (limit,)) as cursor:
                async for row in cursor:
                    traces.append(DecisionTrace.model_validate_json(row[0]))
        return traces

    async def log_human_review(self, trace_id: str, approved: bool, reviewer_id: str, reason: str):
        """Update review fields."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                UPDATE decision_traces 
                SET human_reviewed = 1, review_approved = ?, reviewer_id = ?, review_reason = ?
                WHERE trace_id = ?
            ''', (1 if approved else 0, reviewer_id, reason, trace_id))
            await db.commit()

    async def get_stats(self) -> DashboardStats:
        """Aggregates dashboard statistics from reviewed and unreviewed traces."""
        stats = DashboardStats()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT COUNT(*), AVG(json_extract(trace_json, "$.total_latency_ms")) '
                'FROM decision_traces'
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    stats.total_evaluations = row[0]
                    stats.avg_latency_ms = row[1] or 0.0

            async with db.execute(
                'SELECT decision, COUNT(*) FROM decision_traces GROUP BY decision'
            ) as cursor:
                async for row in cursor:
                    stats.decisions[row[0]] = row[1]

            # A reviewer approving something Sentinel stopped means Sentinel was wrong
            # to stop it: a false positive. A reviewer rejecting something Sentinel let
            # through means it should have been caught: a false negative. Anything else
            # is a confirmed decision.
            stopped = ("BLOCK", "ESCALATE", "REDACT", "WARN")
            confirmed_alerts = 0
            reviewed_alerts = 0
            async with db.execute(
                'SELECT decision, review_approved, COUNT(*) FROM decision_traces '
                'WHERE human_reviewed = 1 GROUP BY decision, review_approved'
            ) as cursor:
                async for decision, approved, count in cursor:
                    was_alert = decision in stopped
                    if was_alert:
                        reviewed_alerts += count
                    if approved == 1:
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
