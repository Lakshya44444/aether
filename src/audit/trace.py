import json
import sqlite3
import aiosqlite
from typing import Optional, List
from src.models.schemas import DecisionTrace, DashboardStats
from src.config import config

class AuditLogger:
    """Decision Trace + SQLite audit log (Section 5.8)."""
    
    def __init__(self):
        self.db_path = config.audit_db_path
        
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
                    review_reason TEXT DEFAULT NULL
                );
            ''')
            await db.commit()

    async def log_trace(self, trace: DecisionTrace):
        """Insert immutable row."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO decision_traces (
                    trace_id, timestamp, session_id, use_case, decision, trace_json
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                trace.trace_id,
                trace.timestamp.isoformat(),
                trace.session_id,
                trace.use_case.value,
                trace.decision.value,
                trace.model_dump_json()
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
        """Get dashboard stats."""
        stats = DashboardStats()
        async with aiosqlite.connect(self.db_path) as db:
            # Basic counts
            async with db.execute('SELECT COUNT(*), AVG(json_extract(trace_json, "$.total_latency_ms")) FROM decision_traces') as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    stats.total_evaluations = row[0]
                    stats.avg_latency_ms = row[1] or 0.0

            # Decisions
            async with db.execute('SELECT decision, COUNT(*) FROM decision_traces GROUP BY decision') as cursor:
                async for row in cursor:
                    stats.decisions[row[0]] = row[1]
                    
            # Human reviews (FP / FN)
            async with db.execute('SELECT review_approved, COUNT(*) FROM decision_traces WHERE human_reviewed = 1 GROUP BY review_approved') as cursor:
                async for row in cursor:
                    if row[0] == 1: # approved despite being blocked/escalated -> FP
                        stats.false_positive_count += row[1]
                    else: # not approved, so the block was justified, or it's a FN if allowed? 
                        pass # Simplified for demo

            # Recent traces
            stats.recent_traces = await self.get_recent_traces(10)
            
            if stats.total_evaluations > 0:
                stats.alert_to_incident_rate = (stats.decisions.get("BLOCK", 0) + stats.decisions.get("ESCALATE", 0)) / stats.total_evaluations

        return stats
