import asyncio
import time
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from src.models.schemas import (
    EvaluationRequest, EvaluationResponse, 
    InputGuardrailRequest, InputGuardrailResponse,
    HumanReviewRequest, HumanReviewResponse,
    DecisionTrace, RiskAssessment, Decision,
    DashboardStats, RiskTier, UseCase, CorrectionResult
)
from src.config import config

# Import modules (mock paths for detectors based on prompt description)
from src.risk_fabric.session_tracker import SessionTracker
from src.risk_fabric.action_impact import get_action_profile
from src.policy_engine.engine import PolicyEngine
from src.verification_router.router import VerificationRouter
from src.audit.trace import AuditLogger
from src.correction.cove_revise import CoVeReviser
from src.correction.bias_resample import BiasResampler
from src.correction.redact import apply_redaction

# Assuming detectors are available
from src.detectors.factuality import FactualityDetector
from src.detectors.privacy import PrivacyDetector
from src.detectors.bias import BiasDetector
from src.detectors.cost import CostDetector
from src.input_guardrail.guardrail import InputGuardrail

app = FastAPI(title="Sentinel AI Runtime Control Plane")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

# Initialize components
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
input_guardrail = InputGuardrail()

@app.on_event("startup")
async def startup_event():
    await audit_logger.init_db()

@app.post("/api/evaluate", response_model=EvaluationResponse)
async def evaluate(request: EvaluationRequest):
    start_time = time.time()
    
    # 1. Extract Request Context
    # Fallback risk tier based on use case policy (if not specified, assuming high for safety)
    policy_config = policy_engine.policies.get(request.use_case.value, {})
    risk_tier_str = policy_config.get("risk_tier", RiskTier.HIGH.value)
    risk_tier = RiskTier(risk_tier_str)
    
    # 2. Route Verification Depth
    depth = verification_router.route(request.use_case, request.action, risk_tier)
    
    # 3. Run Detectors in Parallel
    detector_names = ["factuality", "privacy", "bias", "cost"]
    detectors_tasks = [
        factuality_detector.detect(request.input_text, request.output_text, depth=depth),
        privacy_detector.detect(request.input_text, request.output_text),
        bias_detector.detect(request.input_text, request.output_text),
        cost_detector.detect(
            request.input_text,
            request.output_text,
            session_id=request.session_id,
            model_name=request.model_name,
        )
    ]
    detection_results = await asyncio.gather(*detectors_tasks, return_exceptions=True)

    valid_results = [res for res in detection_results if not isinstance(res, Exception)]
    failed_detectors = [
        name for name, res in zip(detector_names, detection_results)
        if isinstance(res, Exception)
    ]

    # 4. Build Risk Assessment
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
    
    # 5. Run Policy Engine
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

    # 6. Attempt Correction if BLOCK or ESCALATE
    correction_result = None
    corrected_output = request.output_text
    
    if decision in (Decision.BLOCK, Decision.ESCALATE, Decision.REDACT):
        # Gather flagged spans by category
        fact_spans = []
        bias_spans = []
        for res in valid_results:
            if res.flagged:
                if res.category.value == "factuality":
                    fact_spans.extend(res.flagged_spans)
                elif res.category.value == "bias":
                    bias_spans.extend(res.flagged_spans)
                    
        # Apply corrections
        if fact_spans:
            c_res = await cove_reviser.revise(corrected_output, fact_spans, request.context_documents)
            if c_res.succeeded:
                corrected_output = c_res.corrected_text
                correction_result = c_res
                
        if bias_spans:
            b_res = await bias_resampler.resample(corrected_output, bias_spans)
            if b_res.succeeded:
                corrected_output = b_res.corrected_text
                correction_result = b_res
                
    # A REDACT decision has to actually mask something. The privacy detector already
    # reports exact offsets, so the masked text is what the caller receives.
    if decision == Decision.REDACT:
        privacy_spans = [
            span
            for res in valid_results
            if res.category.value == "privacy"
            for span in res.flagged_spans
        ]
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

    latency_ms = (time.time() - start_time) * 1000
    
    # 7. Log Decision Trace
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
    
    # 8. Return Response
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

@app.get("/api/stats", response_model=DashboardStats)
async def get_stats():
    return await audit_logger.get_stats()

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    return session_tracker.get_session_info(session_id)

# Mount dashboard if exists
if os.path.exists("dashboard"):
    app.mount("/", StaticFiles(directory="dashboard", html=True), name="dashboard")
