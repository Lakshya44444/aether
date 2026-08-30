"""The Postgres and Redis backends, against real servers.

Skipped unless AETHER_TEST_PG_DSN / AETHER_TEST_REDIS_URL point at something running.
A mocked database proves the mock works; the things worth testing here are exactly the
things a mock would paper over -- that `SELECT ... FOR UPDATE` actually serialises two
appenders, and that Postgres's `->>` returns text where SQLite's json_extract returns a
number.

    docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=aether -e POSTGRES_USER=aether \
        -e POSTGRES_DB=aether postgres:16-alpine
    docker run -d -p 6399:6379 redis:7-alpine
    AETHER_TEST_PG_DSN=postgresql://aether:aether@localhost:55432/aether \
    AETHER_TEST_REDIS_URL=redis://localhost:6399 pytest tests/test_backends.py
"""
import asyncio
import os
import uuid

import pytest

from src.audit.backends import PostgresBackend, SqliteBackend
from src.audit.trace import AuditLogger
from src.detectors.cost import CostDetector
from src.models.schemas import (
    ActionType, Decision, DecisionTrace, DetectionResult, FlaggedSpan, RiskAssessment,
    RiskCategory, RiskTier, Trajectory, UseCase, VerificationDepth,
    ActionImpact, ActionReversibility,
)
from src.risk_fabric.session_tracker import SessionTracker
from src.state import MemoryStore, RedisStore

PG_DSN = os.environ.get("AETHER_TEST_PG_DSN", "")
REDIS_URL = os.environ.get("AETHER_TEST_REDIS_URL", "")

needs_pg = pytest.mark.skipif(not PG_DSN, reason="set AETHER_TEST_PG_DSN")
needs_redis = pytest.mark.skipif(not REDIS_URL, reason="set AETHER_TEST_REDIS_URL")


def _trace(decision=Decision.ALLOW, session="s", output="fine") -> DecisionTrace:
    result = DetectionResult(category=RiskCategory.PRIVACY, score=0.0, flagged=False)
    assessment = RiskAssessment(
        current_turn_risk=0.0, session_exposure=0.0, trajectory=Trajectory.STABLE,
        action=ActionType.GENERATE_TEXT, action_impact=ActionImpact.LOW,
        action_reversibility=ActionReversibility.HIGH, detection_results=[result],
        use_case=UseCase.CUSTOMER_SUPPORT, risk_tier=RiskTier.LIMITED,
        verification_depth=VerificationDepth.SHALLOW,
    )
    return DecisionTrace(
        session_id=session, use_case=UseCase.CUSTOMER_SUPPORT, risk_tier=RiskTier.LIMITED,
        action=ActionType.GENERATE_TEXT, input_text="q", output_text=output,
        detection_results=[result], risk_assessment=assessment,
        policy_id="p", decision=decision, reason="r", total_latency_ms=1.0,
    )


async def _fresh_pg() -> PostgresBackend:
    backend = PostgresBackend(PG_DSN)
    await backend.init()
    pool = await backend._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE decision_traces, audit_head")
        await conn.execute(
            "INSERT INTO audit_head (id, row_hash, row_count) VALUES (1, $1, 0)", "0" * 64)
    return backend


# ── Postgres: the chain ──────────────────────────────────────────────────────

@needs_pg
@pytest.mark.asyncio
async def test_postgres_chain_verifies():
    logger = AuditLogger(await _fresh_pg())
    for i in range(5):
        await logger.log_trace(_trace(session=f"s{i}"))
    ok, checked, first_bad = await logger.verify_chain()
    assert ok and checked == 5, (checked, first_bad)
    await logger.backend.close()


@needs_pg
@pytest.mark.asyncio
async def test_postgres_detects_a_rewritten_row():
    backend = await _fresh_pg()
    logger = AuditLogger(backend)
    ids = [(await _log_and_return(logger, f"s{i}")) for i in range(4)]

    pool = await backend._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT trace_json FROM decision_traces WHERE trace_id = $1", ids[1])
        await conn.execute("UPDATE decision_traces SET trace_json = $1 WHERE trace_id = $2",
                           row["trace_json"].replace('"reason"', '"rEason"'), ids[1])

    ok, _, first_bad = await logger.verify_chain()
    assert not ok and first_bad == ids[1]
    await backend.close()


async def _log_and_return(logger, session):
    trace = _trace(session=session)
    await logger.log_trace(trace)
    return trace.trace_id


@needs_pg
@pytest.mark.asyncio
async def test_postgres_serialises_concurrent_appenders():
    """The whole reason Postgres exists here.

    Twenty appends launched at once. With SQLite's process-local lock this is what a
    second worker looks like; the row lock has to make the chain come out linear
    anyway.
    """
    backend = await _fresh_pg()
    logger = AuditLogger(backend)
    await asyncio.gather(*[logger.log_trace(_trace(session=f"c{i}")) for i in range(20)])

    ok, checked, first_bad = await logger.verify_chain()
    assert ok, f"chain forked under concurrency at {first_bad}"
    assert checked == 20
    await backend.close()


@needs_pg
@pytest.mark.asyncio
async def test_postgres_stats_read_the_review_verdict_correctly():
    """`->>` yields the strings 'true'/'false'. 'false' is truthy in Python, so a
    naive read counts every rejected review as an approval -- the exact inverse."""
    backend = await _fresh_pg()
    logger = AuditLogger(backend)

    approved = _trace(decision=Decision.BLOCK, session="a")
    rejected = _trace(decision=Decision.ALLOW, session="b")
    await logger.log_trace(approved)
    await logger.log_trace(rejected)
    await logger.log_human_review(approved.trace_id, True, "rev", "over-blocked")
    await logger.log_human_review(rejected.trace_id, False, "rev", "should have caught it")

    stats = await logger.get_stats()
    assert stats.false_positive_count == 1, "approved BLOCK is a false positive"
    assert stats.false_negative_count == 1, "rejected ALLOW is a false negative"
    assert (await logger.verify_chain())[0]
    await backend.close()


@needs_pg
@pytest.mark.asyncio
async def test_postgres_and_sqlite_agree_on_stats(tmp_path):
    """Two backends, one chain rule. They must not drift on what the numbers mean."""
    sqlite = AuditLogger(SqliteBackend(str(tmp_path / "a.db")))
    await sqlite.init_db()
    postgres = AuditLogger(await _fresh_pg())

    for logger in (sqlite, postgres):
        blocked = _trace(decision=Decision.BLOCK, session="x")
        allowed = _trace(decision=Decision.ALLOW, session="y")
        await logger.log_trace(blocked)
        await logger.log_trace(allowed)
        await logger.log_human_review(blocked.trace_id, True, "rev", "")

    a, b = await sqlite.get_stats(), await postgres.get_stats()
    assert (a.total_evaluations, a.false_positive_count, a.false_negative_count) == \
           (b.total_evaluations, b.false_positive_count, b.false_negative_count)
    assert a.decisions == b.decisions
    await postgres.backend.close()


@needs_pg
@pytest.mark.asyncio
async def test_postgres_masks_stored_pii():
    backend = await _fresh_pg()
    logger = AuditLogger(backend)
    result = DetectionResult(
        category=RiskCategory.PRIVACY, score=1.0, flagged=True,
        flagged_spans=[FlaggedSpan(start=9, end=20, text="412-88-7391",
                                   categories=[RiskCategory.PRIVACY], severity=1.0,
                                   detail="Found PII of type SSN")],
    )
    trace = _trace(output="His SSN 412-88-7391 is on file.")
    trace.detection_results = [result]
    await logger.log_trace(trace)

    stored = await logger.get_trace(trace.trace_id)
    assert "412-88-7391" not in stored.output_text
    assert "[SSN]" in stored.output_text
    await backend.close()


# ── Redis: shared session state ──────────────────────────────────────────────

@needs_redis
@pytest.mark.asyncio
async def test_redis_shares_a_session_between_workers():
    """Two SessionTracker instances stand in for two uvicorn workers. With a dict
    each, worker B sees turn 1 of the conversation; with Redis it sees turn 2."""
    prefix = f"test-{uuid.uuid4().hex[:8]}"
    worker_a = SessionTracker(RedisStore(REDIS_URL, prefix))
    worker_b = SessionTracker(RedisStore(REDIS_URL, prefix))

    risky = [DetectionResult(category=RiskCategory.PRIVACY, score=0.9, flagged=True)]
    await worker_a.update("shared", UseCase.CUSTOMER_SUPPORT, risky)
    _, exposure, _ = await worker_b.update("shared", UseCase.CUSTOMER_SUPPORT, risky)

    info = await worker_b.get_session_info("shared")
    assert info.turn_count == 2, "worker B did not see worker A's turn"
    assert exposure > 0.54 * 0.9, "exposure did not accumulate across workers"

    await worker_a.forget("shared")
    await worker_a.store.close()
    await worker_b.store.close()


@needs_redis
@pytest.mark.asyncio
async def test_redis_shares_cost_accounting_between_workers():
    prefix = f"test-{uuid.uuid4().hex[:8]}"
    a = CostDetector(RedisStore(REDIS_URL, prefix))
    b = CostDetector(RedisStore(REDIS_URL, prefix))

    for _ in range(3):
        await a.detect("same prompt", "reply", session_id="shared")
    result = await b.detect("same prompt", "reply", session_id="shared")

    assert result.details["retry_count"] == 3, "retries did not carry across workers"
    assert result.details["session_cost_usd"] > result.details["estimated_cost_usd"]

    await b.forget_session("shared")
    await a.store.close()
    await b.store.close()


@needs_redis
@pytest.mark.asyncio
async def test_redis_and_memory_stores_behave_identically():
    prefix = f"test-{uuid.uuid4().hex[:8]}"
    for store in (MemoryStore(), RedisStore(REDIS_URL, prefix)):
        assert await store.get("absent") is None
        await store.put("k", {"n": 1}, 60)
        assert await store.get("k") == {"n": 1}
        await store.read_modify_write("k", 60, lambda cur: {"n": cur["n"] + 1})
        assert (await store.get("k"))["n"] == 2
        await store.delete("k")
        assert await store.get("k") is None
        await store.close()
