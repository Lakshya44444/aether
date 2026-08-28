"""End-to-end regression tests for the evaluate pipeline.

These exercise the whole gateway path, because several defects were only visible
once detection, policy, correction and audit ran together.
"""
import asyncio
import os
import sqlite3
import tempfile

import pytest

os.environ.setdefault(
    "SENTINEL_AUDIT_DB_PATH", os.path.join(tempfile.mkdtemp(), "regression_audit.db")
)

import src.main as M
from src.config import config
from src.models.schemas import (
    ActionType, CorrectionResult, Decision, EvaluationRequest, UseCase,
)


@pytest.fixture(autouse=True)
async def _clean_state():
    await M.audit_logger.init_db()
    M.session_tracker.sessions.clear()
    M.cost_detector.session_costs.clear()
    M.cost_detector.session_inputs.clear()
    yield


def _request(**kwargs):
    base = dict(
        input_text="what is the status?",
        output_text="Everything looks fine.",
        use_case=UseCase.CUSTOMER_SUPPORT,
        action=ActionType.GENERATE_TEXT,
    )
    base.update(kwargs)
    return EvaluationRequest(**base)


async def _with_broken_detectors(names, request):
    async def boom(*args, **kwargs):
        raise RuntimeError("detector down")

    saved = {n: getattr(M, n).detect for n in names}
    for n in names:
        setattr(getattr(M, n), "detect", boom)
    try:
        return await M.evaluate(request)
    finally:
        for n, fn in saved.items():
            setattr(getattr(M, n), "detect", fn)


@pytest.mark.asyncio
async def test_fail_closed_blocks_when_all_detectors_raise():
    """A total detection blackout previously returned ALLOW / 'All checks passed'."""
    response = await _with_broken_detectors(
        ["factuality_detector", "privacy_detector", "bias_detector", "cost_detector"],
        _request(use_case=UseCase.INTERNAL_COPILOT, session_id="fc"),
    )
    assert response.decision == Decision.BLOCK
    assert response.trace.failed_detectors


@pytest.mark.asyncio
async def test_detector_failure_is_recorded_on_the_trace():
    """An audit row must not imply checks ran when they did not."""
    response = await _with_broken_detectors(
        ["factuality_detector"], _request(session_id="rec")
    )
    assert "factuality" in " ".join(response.trace.failed_detectors)


@pytest.mark.asyncio
async def test_fail_open_still_signals_the_failure():
    response = await _with_broken_detectors(
        ["factuality_detector"], _request(session_id="fo")
    )
    assert response.decision != Decision.ALLOW


@pytest.mark.asyncio
async def test_latency_budget_is_enforced():
    """latency_budget_ms was configured in every policy and read by nothing."""
    original = M.bias_detector.detect

    async def slow(*args, **kwargs):
        await asyncio.sleep(5)
        return await original(*args, **kwargs)

    M.bias_detector.detect = slow
    try:
        response = await M.evaluate(_request(
            use_case=UseCase.FINANCE_AGENT, action=ActionType.EXECUTE_PAYMENT,
            session_id="to",
        ))
    finally:
        M.bias_detector.detect = original

    assert response.trace.total_latency_ms < 3000
    assert any("timeout" in name for name in response.trace.failed_detectors)


@pytest.mark.asyncio
async def test_redact_decision_returns_masked_text():
    """REDACT previously returned corrected_output=None with the PII intact."""
    response = await M.evaluate(_request(
        output_text="Your email is john.doe@acme.com and we have it on file.",
        session_id="rd",
    ))
    assert response.decision == Decision.REDACT
    assert response.corrected_output is not None
    assert "john.doe@acme.com" not in response.corrected_output


@pytest.mark.asyncio
async def test_irreversible_action_is_not_released_on_a_redact():
    """REDACT was absent from both safety-upgrade guards in the policy engine."""
    response = await M.evaluate(_request(
        use_case=UseCase.INTERNAL_COPILOT,
        action=ActionType.DELETE_RECORD,
        output_text="Deleting the record for jane@acme.com now.",
        session_id="del",
    ))
    assert response.decision in (Decision.ESCALATE, Decision.BLOCK)


@pytest.mark.asyncio
async def test_correction_is_rejected_when_it_does_not_help():
    """A corrector's self-declared success used to be accepted unverified."""
    original = M.bias_resampler.resample

    async def liar(text, spans):
        return CorrectionResult(
            attempted=True, succeeded=True, original_text=text,
            corrected_text=text + " ", method="liar",
        )

    M.bias_resampler.resample = liar
    try:
        response = await M.evaluate(_request(
            use_case=UseCase.INTERNAL_COPILOT, action=ActionType.SEND_EMAIL,
            output_text="She should be rejected for the role.", session_id="liar",
        ))
    finally:
        M.bias_resampler.resample = original

    assert response.trace.correction is not None
    assert response.trace.correction.succeeded is False
    assert response.decision == Decision.BLOCK


@pytest.mark.asyncio
async def test_audit_chain_detects_a_rewritten_row():
    """'Insert immutable row' was a docstring, not a property."""
    for i in range(4):
        await M.evaluate(_request(output_text=f"Reply {i}.", session_id=f"chain{i}"))

    ok, checked, _ = await M.audit_logger.verify_chain()
    assert ok and checked >= 4

    db = sqlite3.connect(config.audit_db_path)
    trace_id, payload = db.execute(
        "SELECT trace_id, trace_json FROM decision_traces ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    db.execute(
        "UPDATE decision_traces SET trace_json=? WHERE trace_id=?",
        (payload.replace('"reason"', '"rEason"'), trace_id),
    )
    db.commit()
    db.close()

    ok, _, first_bad = await M.audit_logger.verify_chain()
    assert not ok
    assert first_bad == trace_id


@pytest.mark.asyncio
async def test_cost_does_not_leak_between_sessions_through_the_gateway():
    for i in range(3):
        await M.evaluate(_request(
            input_text=f"tenant A query {i}", output_text="x " * 400, session_id="tenantA",
        ))
    response = await M.evaluate(_request(
        input_text="tenant B first query", output_text="hi", session_id="tenantB",
    ))
    cost = next(d for d in response.trace.detection_results if d.category.value == "cost")
    assert cost.details["session_cost_usd"] == pytest.approx(
        cost.details["estimated_cost_usd"]
    )
