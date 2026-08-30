import hashlib
import json
from typing import Optional, List
from src.audit.backends import ChainBackend, open_backend
from src.correction.redact import apply_redaction, mask_label

from src.models.schemas import DecisionTrace, DashboardStats, RiskCategory
from src.config import config


_PII_CATEGORIES = (RiskCategory.PRIVACY, RiskCategory.INPUT_PRIVACY)


def _mask_span_text(result):
    """Replaces the quoted characters of a PII span with the label that masked them.

    Only the PII categories: a factuality or bias span quotes text that stays verbatim
    in the completion anyway, so masking its copy would hide nothing that is not
    already stored beside it.
    """
    if result.category not in _PII_CATEGORIES or not result.flagged_spans:
        return result
    return result.model_copy(update={
        "flagged_spans": [s.model_copy(update={"text": mask_label(s)})
                          for s in result.flagged_spans],
    })

_GENESIS = "0" * 64


class AuditLogger:
    """Decision Trace + hash-chained audit log.

    Rows are hash-chained: each row stores the hash of the previous row together with
    its own content. Neither SQLite nor Postgres can prevent an UPDATE or DELETE, so
    tamper-evidence is provided instead of a claim of immutability the storage layer
    cannot keep.

    Everything that can change the published metrics is inside the chain, including
    human review verdicts. A review is an appended row, not an update to the row it
    reviews, because a mutable column is outside the hash and can be forged silently.

    Where the rows live is `src/audit/backends.py`'s problem. This class owns the chain
    rule and nothing else, so SQLite and Postgres cannot drift apart on what a valid
    chain is.
    """

    def __init__(self, backend: Optional[ChainBackend] = None):
        self.backend = backend or open_backend(config.audit_dsn, config.audit_db_path)

    @property
    def db_path(self) -> str:
        """Kept for the tests and scripts that open the SQLite file directly."""
        return getattr(self.backend, "path", "")

    async def init_db(self):
        """Creates whatever the configured backend needs."""
        await self.backend.init()

    @staticmethod
    def _hash_row(prev_hash: str, trace_json: str) -> str:
        return hashlib.sha256(f"{prev_hash}{trace_json}".encode("utf-8")).hexdigest()

    async def verify_chain(self):
        """Recomputes the chain and reports the first row that does not match.

        Returns (ok, checked, first_bad_trace_id).
        """
        # ponytail: the head lives in the same file it protects, so this detects
        # accidental corruption and unsophisticated tampering, not an attacker with
        # write access to the whole database. Anchoring the head externally -- a signed
        # checkpoint, or an append to write-once storage -- is the upgrade path.
        rows = await self.backend.fetch_all_rows()
        head_hash, head_count = await self.backend.head()

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

    @staticmethod
    def _for_storage(trace: DecisionTrace) -> DecisionTrace:
        """Masks detected PII in the text a row persists.

        The audit log is the one place every prompt and completion the gateway has ever
        seen accumulates, and /api/traces reads it back. Storing that text verbatim made
        the governance record the largest PII store in the system. Offsets, categories
        and severities are untouched, so a reviewer can still see what was found and
        where; only the characters the detectors identified are masked.

        That includes the span's own `text`. Masking the completion while leaving the
        span quoting it verbatim stored the one substring guaranteed to be PII -- the
        part detection actually caught -- in the clear, and served it from /api/traces.

        Offsets index the text the span was found in: privacy spans index the
        completion, input_privacy and injection spans index the prompt.
        """
        if not config.audit_redact_stored_text:
            return trace

        output_spans = []
        input_spans = []
        for result in trace.detection_results:
            if result.category == RiskCategory.PRIVACY:
                output_spans.extend(result.flagged_spans)
            elif result.category == RiskCategory.INPUT_PRIVACY:
                input_spans.extend(result.flagged_spans)

        if not output_spans and not input_spans:
            return trace

        masked_results = [_mask_span_text(r) for r in trace.detection_results]
        update = {
            "input_text": apply_redaction(trace.input_text, input_spans),
            "output_text": apply_redaction(trace.output_text, output_spans),
            "detection_results": masked_results,
            # The risk assessment carries its own copy of the same results. Masking one
            # list and not the other left the PII in the row regardless.
            "risk_assessment": trace.risk_assessment.model_copy(
                update={"detection_results": masked_results}
            ),
        }
        # A correction carries its own copy of the completion, which is the same text
        # and the same offsets. Masking the trace but not this one would have left the
        # raw value in the row anyway.
        if trace.correction and trace.correction.original_text == trace.output_text:
            update["correction"] = trace.correction.model_copy(
                update={"original_text": update["output_text"]}
            )
        return trace.model_copy(update=update)

    async def log_trace(self, trace: DecisionTrace):
        """Appends a tamper-evident row linked to the one before it."""
        trace_json = self._for_storage(trace).model_dump_json()
        # The backend takes whatever lock keeps two appenders from reading the same
        # tail hash: a process-local lock for SQLite, a row lock for Postgres.
        await self.backend.append(
            trace_id=trace.trace_id,
            timestamp=trace.timestamp.isoformat(),
            session_id=trace.session_id,
            use_case=trace.use_case.value,
            decision=trace.decision.value,
            payload_json=trace_json,
            kind='trace',
            hash_row=self._hash_row,
        )

    async def get_trace(self, trace_id: str) -> Optional[DecisionTrace]:
        """Get specific trace."""
        rows = await self.backend.query(
            "SELECT trace_json FROM decision_traces WHERE trace_id = ? AND kind = 'trace'",
            (trace_id,),
        )
        return DecisionTrace.model_validate_json(rows[0][0]) if rows else None

    async def get_recent_traces(self, limit=50) -> List[DecisionTrace]:
        """Get recent traces."""
        rows = await self.backend.query(
            "SELECT trace_json FROM decision_traces WHERE kind = 'trace' "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        return [DecisionTrace.model_validate_json(row[0]) for row in rows]

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

        parent = await self.backend.query(
            "SELECT session_id, use_case FROM decision_traces "
            "WHERE trace_id = ? AND kind = 'trace'",
            (trace_id,),
        )
        session_id, use_case = parent[0] if parent else ("", "")

        counted = await self.backend.query(
            "SELECT COUNT(*) FROM decision_traces WHERE kind = 'review' "
            f"AND {self.backend.json_field('trace_json', 'trace_id')} = ?",
            (trace_id,),
        )
        seq = counted[0][0]

        await self.backend.append(
            trace_id=f"review:{trace_id}:{seq}",
            timestamp=stamp,
            session_id=session_id,
            use_case=use_case,
            decision="REVIEW",
            payload_json=payload,
            kind='review',
            hash_row=self._hash_row,
        )

    async def get_review(self, trace_id: str) -> Optional[dict]:
        """Returns the latest chained review verdict for a trace, if any."""
        rows = await self.backend.query(
            "SELECT trace_json FROM decision_traces WHERE kind = 'review' "
            f"AND {self.backend.json_field('trace_json', 'trace_id')} = ? "
            f"ORDER BY {self.backend.order_by} DESC LIMIT 1",
            (trace_id,),
        )
        return json.loads(rows[0][0]) if rows else None

    async def get_stats(self) -> DashboardStats:
        """Aggregates dashboard statistics from reviewed and unreviewed traces."""
        stats = DashboardStats()
        backend = self.backend

        totals = await backend.query(
            f"SELECT COUNT(*), AVG({backend.json_number('trace_json', 'total_latency_ms')}) "
            "FROM decision_traces WHERE kind = 'trace'"
        )
        if totals and totals[0][0]:
            stats.total_evaluations = totals[0][0]
            stats.avg_latency_ms = totals[0][1] or 0.0

        for decision, count in await backend.query(
            "SELECT decision, COUNT(*) FROM decision_traces WHERE kind = 'trace' "
            "GROUP BY decision"
        ):
            stats.decisions[decision] = count

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
        approved_field = backend.json_field("r.trace_json", "approved")
        tid_field = backend.json_field("trace_json", "trace_id")
        rows = await backend.query(f'''
            SELECT t.decision, {approved_field}, COUNT(*)
            FROM decision_traces t
            JOIN (
                SELECT trace_json,
                       {tid_field} AS tid,
                       ROW_NUMBER() OVER (
                           PARTITION BY {tid_field}
                           ORDER BY {backend.order_by} DESC
                       ) AS rn
                FROM decision_traces WHERE kind = 'review'
            ) r ON r.tid = t.trace_id AND r.rn = 1
            WHERE t.kind = 'trace'
            GROUP BY t.decision, {approved_field}
        ''')
        for decision, approved, count in rows:
            # SQLite's json_extract gives 1/0; Postgres's ->> gives the strings
            # 'true'/'false', and 'false' is truthy in Python. Normalising here rather
            # than trusting the driver is the difference between a false-positive count
            # and its exact inverse.
            was_approved = approved in (1, True, "true")
            was_alert = decision in stopped
            if was_alert:
                reviewed_alerts += count
            if was_approved:
                if was_alert:
                    stats.false_positive_count += count
                # An allowed response the reviewer also approves is simply correct.
            elif was_alert:
                confirmed_alerts += count
            else:
                stats.false_negative_count += count

        reviewed = await backend.query(
            f"SELECT DISTINCT {tid_field} FROM decision_traces WHERE kind = 'review'"
        )
        stats.reviewed_trace_ids = [row[0] for row in reviewed if row[0]]

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
