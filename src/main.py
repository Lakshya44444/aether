import asyncio
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from src.models.schemas import (
    EvaluationRequest, EvaluationResponse, 
    InputGuardrailRequest, InputGuardrailResponse,
    HumanReviewRequest, HumanReviewResponse,
    DecisionTrace, RiskAssessment, Decision,
    DashboardStats, RiskTier, UseCase, CorrectionResult, VerificationDepth
)
from src.config import config

from src.risk_fabric.session_tracker import SessionTracker
from src.risk_fabric.action_impact import get_action_profile
from src.policy_engine.engine import PolicyEngine
from src.verification_router.router import VerificationRouter
from src.audit.trace import AuditLogger
from src.correction.cove_revise import CoVeReviser
from src.correction.bias_resample import BiasResampler
from src.correction.redact import apply_redaction

from src.detectors.factuality import FactualityDetector
from src.detectors.privacy import PrivacyDetector
from src.detectors.bias import BiasDetector
from src.detectors.cost import CostDetector
from src.detectors.injection import InjectionDetector
from src.input_guardrail.guardrail import InputGuardrail

@asynccontextmanager
async def lifespan(_: FastAPI):
    await audit_logger.init_db()
    yield

app = FastAPI(title="Sentinel AI Runtime Control Plane", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in config.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

_SEVERITY = {
    Decision.ALLOW: 0,
    Decision.WARN: 1,
    Decision.REDACT: 2,
    Decision.ESCALATE: 3,
    Decision.BLOCK: 4,
}

session_tracker = SessionTracker()
policy_engine = PolicyEngine(config.policies_dir)
verification_router = VerificationRouter()
audit_logger = AuditLogger()
cove_reviser = CoVeReviser()
bias_resampler = BiasResampler()

factuality_detector = FactualityDetector()
privacy_detector = PrivacyDetector()
bias_detector = BiasDetector()
cost_detector = CostDetector()
injection_detector = InjectionDetector()
input_guardrail = InputGuardrail()

@app.post("/api/evaluate", response_model=EvaluationResponse)
async def evaluate(request: EvaluationRequest):
    start_time = time.time()
    
    policy_config = policy_engine.policies.get(request.use_case.value, {})
    risk_tier_str = policy_config.get("risk_tier", RiskTier.HIGH.value)
    risk_tier = RiskTier(risk_tier_str)
    
    depth = verification_router.route(request.use_case, request.action, risk_tier)
    
    # The last two screen the prompt rather than the completion. Without them
    # /api/evaluate looked only at what the model said, so a prompt carrying PII and
    # "ignore all prior instructions" returned ALLOW / "All checks passed" -- the input
    # guardrail existed only as a separate endpoint an integrator had no reason to call.
    detector_names = ["factuality", "privacy", "bias", "cost", "input_privacy", "injection"]
    detectors_tasks = [
        factuality_detector.detect(
            request.input_text, request.output_text,
            depth=depth, context_documents=request.context_documents,
        ),
        privacy_detector.detect(request.input_text, request.output_text),
        bias_detector.detect(request.input_text, request.output_text),
        cost_detector.detect(
            request.input_text,
            request.output_text,
            session_id=request.session_id,
            model_name=request.model_name,
        ),
        privacy_detector.detect("", request.input_text),
        injection_detector.detect(request.input_text, ""),
    ]
    # A detector that overruns its budget is treated exactly like one that raised: it
    # produced no signal, so the declared fail mode decides rather than the absence of
    # a flag. Note this only bites at an await point -- the detectors are CPU-bound and
    # currently never yield, so the timeout cannot actually interrupt them.
    default_budget_ms = {
        VerificationDepth.SHALLOW: config.shallow_latency_budget_ms,
        VerificationDepth.MEDIUM: config.medium_latency_budget_ms,
        VerificationDepth.DEEP: config.deep_latency_budget_ms,
    }[depth]
    budget_s = policy_config.get("latency_budget_ms", default_budget_ms) / 1000
    detection_results = await asyncio.gather(
        *[asyncio.wait_for(task, timeout=budget_s) for task in detectors_tasks],
        return_exceptions=True,
    )

    by_name = dict(zip(detector_names, detection_results))
    valid_results = [res for res in detection_results if not isinstance(res, Exception)]

    # Input-side spans carry offsets into the prompt, not the completion. Tagging them
    # keeps them out of the output redaction path, where they would mask the wrong
    # characters, while still letting the policy engine score them.
    for name in ("input_privacy", "injection"):
        result = by_name[name]
        if not isinstance(result, Exception):
            result.details = {**result.details, "side": "input"}

    failed_detectors = [
        f"{name} (timeout)" if isinstance(res, asyncio.TimeoutError) else name
        for name, res in zip(detector_names, detection_results)
        if isinstance(res, Exception)
    ]

    impact, reversibility = get_action_profile(request.action)
    turn_risk, session_exposure, trajectory = session_tracker.update(
        request.session_id, request.use_case, valid_results
    )
    
    risk_assessment = RiskAssessment(
        current_turn_risk=turn_risk,
        session_exposure=session_exposure,
        trajectory=trajectory,
        action=request.action,
        action_impact=impact,
        action_reversibility=reversibility,
        detection_results=valid_results,
        use_case=request.use_case,
        risk_tier=risk_tier,
        verification_depth=depth
    )
    
    decision, reason, policy_id = policy_engine.evaluate(risk_assessment)
    fail_mode = policy_config.get("fail_mode", "fail_closed")

    # A detector that raised produced no signal. Treating that as "nothing found" turns
    # every crash into a silent ALLOW, so the policy's declared fail mode is applied here
    # and the failure is recorded on the trace rather than being swallowed.
    if failed_detectors:
        failure_note = f"detector(s) failed: {', '.join(failed_detectors)}"
        if fail_mode == "fail_open":
            if decision == Decision.ALLOW:
                decision = Decision.WARN
                reason = f"{failure_note}; fail_open -> WARN"
            else:
                reason = f"{reason}; {failure_note}"
        else:
            if _SEVERITY[decision] < _SEVERITY[Decision.BLOCK]:
                decision = Decision.BLOCK
            reason = f"{failure_note}; fail_closed -> BLOCK"

    correction_result = None
    corrected_output = request.output_text

    if decision in (Decision.BLOCK, Decision.ESCALATE) and not failed_detectors:
        fact_spans = []
        bias_spans = []
        for res in valid_results:
            if res.flagged and res.details.get("side") != "input":
                if res.category.value == "factuality":
                    fact_spans.extend(res.flagged_spans)
                elif res.category.value == "bias":
                    bias_spans.extend(res.flagged_spans)

        attempted = None
        if fact_spans:
            attempted = await cove_reviser.revise(
                corrected_output, fact_spans, request.context_documents
            )
        if bias_spans:
            base = attempted.corrected_text if attempted and attempted.succeeded else corrected_output
            attempted = await bias_resampler.resample(base, bias_spans)

        # A corrector reporting its own success proves nothing. The revised text is put
        # back through the same detectors and the same policy, and the correction is
        # only accepted if that second pass genuinely lands on a softer decision.
        if attempted and attempted.succeeded and attempted.corrected_text != corrected_output:
            recheck = await asyncio.gather(
                factuality_detector.detect(
                    request.input_text, attempted.corrected_text,
                    depth=depth, context_documents=request.context_documents,
                ),
                privacy_detector.detect(request.input_text, attempted.corrected_text),
                bias_detector.detect(request.input_text, attempted.corrected_text),
                return_exceptions=True,
            )
            recheck_results = [r for r in recheck if not isinstance(r, Exception)]
            if len(recheck_results) == 3:
                recheck_assessment = risk_assessment.model_copy(
                    update={"detection_results": recheck_results}
                )
                new_decision, new_reason, _ = policy_engine.evaluate(recheck_assessment)
                if _SEVERITY[new_decision] < _SEVERITY[decision]:
                    # A corrected response is not a clean response. Returning ALLOW here
                    # told the caller nothing happened, while the text they were handed
                    # had been rewritten -- and a caller that keeps its own copy of the
                    # original would then ship the uncorrected version.
                    new_decision = max(new_decision, Decision.WARN, key=lambda d: _SEVERITY[d])
                    corrected_output = attempted.corrected_text
                    attempted.details = {
                        **attempted.details,
                        "decision_before": decision.value,
                        "decision_after": new_decision.value,
                        "verified_by_recheck": True,
                    }
                    correction_result = attempted
                    decision = new_decision
                    reason = f"{reason}; corrected and re-verified -> {new_reason}"
                else:
                    attempted.succeeded = False
                    attempted.details = {
                        **attempted.details,
                        "verified_by_recheck": False,
                        "note": "correction did not lower the decision; original stands",
                    }
                    correction_result = attempted

    # A REDACT decision has to actually mask something. The privacy detector already
    # reports exact offsets, so the masked text is what the caller receives.
    if decision == Decision.REDACT:
        output_privacy = by_name["privacy"]
        privacy_spans = (
            [] if isinstance(output_privacy, Exception) else list(output_privacy.flagged_spans)
        )
        if privacy_spans:
            corrected_output = apply_redaction(corrected_output, privacy_spans)
            correction_result = CorrectionResult(
                attempted=True,
                succeeded=True,
                original_text=request.output_text,
                corrected_text=corrected_output,
                method="span_redaction",
                details={"spans_masked": len(privacy_spans)},
            )

    session_tracker.record_decision(request.session_id, decision)

    latency_ms = (time.time() - start_time) * 1000

    trace = DecisionTrace(
        request_id="req-" + str(time.time()),
        session_id=request.session_id,
        use_case=request.use_case,
        risk_tier=risk_tier,
        action=request.action,
        input_text=request.input_text,
        output_text=request.output_text,
        detection_results=valid_results,
        risk_assessment=risk_assessment,
        policy_id=policy_id,
        decision=decision,
        reason=reason,
        correction=correction_result,
        fail_mode=fail_mode,
        failed_detectors=failed_detectors,
        total_latency_ms=latency_ms
    )
    await audit_logger.log_trace(trace)
    
    return EvaluationResponse(
        decision=decision,
        reason=reason,
        trace=trace,
        corrected_output=corrected_output if correction_result else None
    )

@app.post("/api/evaluate/input", response_model=InputGuardrailResponse)
async def evaluate_input(request: InputGuardrailRequest):
    return await input_guardrail.screen(request)

@app.get("/api/traces")
async def get_traces():
    return await audit_logger.get_recent_traces(50)

@app.get("/api/traces/{trace_id}")
async def get_trace(trace_id: str):
    trace = await audit_logger.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace

@app.post("/api/review", response_model=HumanReviewResponse)
async def submit_review(request: HumanReviewRequest):
    trace = await audit_logger.get_trace(request.trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
        
    await audit_logger.log_human_review(request.trace_id, request.approved, request.reviewer_id, request.reason)
    return HumanReviewResponse(
        trace_id=request.trace_id,
        original_decision=trace.decision,
        review_outcome="Approved" if request.approved else "Rejected",
        feedback_logged=True
    )

@app.get("/api/audit/verify")
async def verify_audit_chain():
    """Recomputes the audit hash chain and reports the first row that fails."""
    ok, checked, first_bad = await audit_logger.verify_chain()
    return {"intact": ok, "rows_checked": checked, "first_invalid_trace_id": first_bad}


@app.get("/api/stats", response_model=DashboardStats)
async def get_stats():
    return await audit_logger.get_stats()

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    return session_tracker.get_session_info(session_id)

# Serve the built Next.js frontend when it exists, falling back to the legacy
# static dashboard so the gateway still has a UI before the frontend is built.
_FRONTEND = "frontend/out" if os.path.exists("frontend/out") else "dashboard"
if os.path.exists(_FRONTEND):
    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.host, port=config.port)
