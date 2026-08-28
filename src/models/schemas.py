"""
Sentinel — AI Runtime Control Plane
Core data models and schemas.

Every data structure flowing through the 8-layer pipeline is defined here,
providing a single, shared contract for detectors, risk fabric, policy engine,
correction layer, audit log, and the API surface.
"""
from __future__ import annotations

from enum import Enum
from datetime import datetime
from typing import Optional, List, Dict, Any
import uuid

from pydantic import BaseModel, Field


# ╔══════════════════════════════════════════════════════════════════╗
# ║  Enumerations                                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

class Decision(str, Enum):
    """Five-state policy decision output (Section 5.5.1)."""
    ALLOW = "ALLOW"
    WARN = "WARN"
    REDACT = "REDACT"
    ESCALATE = "ESCALATE"
    BLOCK = "BLOCK"


class RiskCategory(str, Enum):
    """Detection categories — each maps to a dedicated detector."""
    FACTUALITY = "factuality"
    PRIVACY = "privacy"
    BIAS = "bias"
    COST = "cost"


class RiskTier(str, Enum):
    """EU AI Act-aligned four-tier risk classification (Regulation 2024/1689)."""
    MINIMAL = "minimal"
    LIMITED = "limited"
    HIGH = "high"
    UNACCEPTABLE = "unacceptable"


class UseCase(str, Enum):
    """Supported enterprise use cases, each with independent policy."""
    CUSTOMER_SUPPORT = "customer_support"
    INTERNAL_COPILOT = "internal_copilot"
    FINANCE_AGENT = "finance_agent"


class ActionType(str, Enum):
    """Actions the AI system can take — ordered by impact × reversibility."""
    GENERATE_TEXT = "generate_text"
    DRAFT_EMAIL = "draft_email"
    SEND_EMAIL = "send_email"
    UPDATE_CRM = "update_crm"
    DELETE_RECORD = "delete_record"
    EXECUTE_PAYMENT = "execute_payment"


class ActionImpact(str, Enum):
    """Impact level of an action (Section 5.4.2)."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionReversibility(str, Enum):
    """How reversible an action is once taken (Section 5.4.2)."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    VERY_LOW = "very_low"


class Trajectory(str, Enum):
    """Risk trajectory direction across session turns (Section 5.4.3)."""
    RISING = "rising"
    FALLING = "falling"
    STABLE = "stable"


class VerificationDepth(str, Enum):
    """Adaptive verification depth — routes between cheap and expensive checks."""
    SHALLOW = "shallow"      # <200ms — regex/pattern + single-pass classifier
    MEDIUM = "medium"        # <700ms — SelfCheckGPT multi-sample consistency
    DEEP = "deep"            # <1000ms — Ragas claim decomposition + evidence


# ╔══════════════════════════════════════════════════════════════════╗
# ║  Detection Models                                                ║
# ╚══════════════════════════════════════════════════════════════════╝

class FlaggedSpan(BaseModel):
    """A span of text flagged by one or more detectors.

    Multi-label by design (Section 5.2.4): a single span can carry
    multiple risk categories simultaneously.
    """
    start: int
    end: int
    text: str
    categories: List[RiskCategory]
    severity: float = Field(ge=0.0, le=1.0)
    detail: str = ""


class DetectionResult(BaseModel):
    """Output from a single detector module.

    Attributes:
        category: Which risk dimension this result covers.
        score: Risk score from 0.0 (safe) to 1.0 (critical).
        flagged: Whether this detector considers the content risky.
        flagged_spans: Specific text spans that triggered the flag.
        details: Detector-specific metadata (branch info, claim counts, etc.).
        branch_used: For factuality — "evidence" or "consistency".
        latency_ms: Wall-clock time this detector took.
    """
    category: RiskCategory
    score: float = Field(ge=0.0, le=1.0)
    flagged: bool
    flagged_spans: List[FlaggedSpan] = []
    details: Dict[str, Any] = {}
    branch_used: Optional[str] = None
    latency_ms: float = 0.0


# ╔══════════════════════════════════════════════════════════════════╗
# ║  Risk Assessment                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝

class RiskAssessment(BaseModel):
    """Full risk assessment — the Risk Fabric's output (Section 5.4).

    Combines detection results with context (use case, action, session
    history) to produce a structured risk picture for the Policy Engine.

    The three history fields (current_turn_risk, session_exposure, trajectory)
    are deliberately kept separate — NOT a single additive number.
    """
    current_turn_risk: float = Field(ge=0.0, le=1.0)
    session_exposure: float = Field(ge=0.0)
    trajectory: Trajectory
    action: ActionType = ActionType.GENERATE_TEXT
    action_impact: ActionImpact
    action_reversibility: ActionReversibility
    detection_results: List[DetectionResult]
    use_case: UseCase
    risk_tier: RiskTier
    verification_depth: VerificationDepth


# ╔══════════════════════════════════════════════════════════════════╗
# ║  Correction                                                      ║
# ╚══════════════════════════════════════════════════════════════════╝

class CorrectionResult(BaseModel):
    """Result of a correction attempt (CoVe / BiasFilter approx).

    If correction fails to bring the response below policy threshold,
    the original ESCALATE or BLOCK decision stands.
    """
    attempted: bool = False
    succeeded: bool = False
    original_text: str = ""
    corrected_text: str = ""
    method: str = ""
    details: Dict[str, Any] = {}


# ╔══════════════════════════════════════════════════════════════════╗
# ║  Decision Trace / Audit Log                                      ║
# ╚══════════════════════════════════════════════════════════════════╝

class DecisionTrace(BaseModel):
    """Complete, immutable record of a single Sentinel decision (Section 5.8).

    Logged for EVERY decision regardless of outcome. Contains:
    detection signals, context, action impact/reversibility,
    session state, matched policy, final decision, and plain-English reason.
    """
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    request_id: str
    session_id: str
    use_case: UseCase
    risk_tier: RiskTier
    action: ActionType
    input_text: str
    output_text: str
    detection_results: List[DetectionResult]
    risk_assessment: RiskAssessment
    policy_id: str
    decision: Decision
    reason: str
    correction: Optional[CorrectionResult] = None
    fail_mode: str = ""
    total_latency_ms: float = 0.0


# ╔══════════════════════════════════════════════════════════════════╗
# ║  API Request / Response Models                                   ║
# ╚══════════════════════════════════════════════════════════════════╝

class EvaluationRequest(BaseModel):
    """Request to evaluate an AI interaction through the full pipeline."""
    input_text: str
    output_text: str
    use_case: UseCase = UseCase.CUSTOMER_SUPPORT
    action: ActionType = ActionType.GENERATE_TEXT
    session_id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()))
    context_documents: Optional[List[str]] = None
    model_name: str = "default"
    metadata: Dict[str, Any] = {}


class EvaluationResponse(BaseModel):
    """Response from the Sentinel evaluation pipeline."""
    decision: Decision
    reason: str
    trace: DecisionTrace
    corrected_output: Optional[str] = None
    warnings: List[str] = []


class InputGuardrailRequest(BaseModel):
    """Request to screen input BEFORE it reaches the AI model (Section 5.1)."""
    input_text: str
    use_case: UseCase = UseCase.CUSTOMER_SUPPORT
    session_id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict[str, Any] = {}


class InputGuardrailResponse(BaseModel):
    """Response from the input-side guardrail."""
    decision: Decision
    reason: str
    sanitized_text: Optional[str] = None
    flagged_spans: List[FlaggedSpan] = []


class HumanReviewRequest(BaseModel):
    """Human reviewer's decision on an escalated case (Section 5.7)."""
    trace_id: str
    approved: bool
    reviewer_id: str = "reviewer"
    reason: str = ""


class HumanReviewResponse(BaseModel):
    """Confirmation of human review processing."""
    trace_id: str
    original_decision: Decision
    review_outcome: str
    feedback_logged: bool


# ╔══════════════════════════════════════════════════════════════════╗
# ║  Dashboard / Stats                                               ║
# ╚══════════════════════════════════════════════════════════════════╝

class DashboardStats(BaseModel):
    """Aggregated statistics for the monitoring dashboard."""
    total_evaluations: int = 0
    decisions: Dict[str, int] = Field(default_factory=lambda: {
        "ALLOW": 0, "WARN": 0, "REDACT": 0, "ESCALATE": 0, "BLOCK": 0
    })
    avg_latency_ms: float = 0.0
    false_positive_count: int = 0
    false_negative_count: int = 0
    alert_to_incident_rate: float = 0.0
    recent_traces: List[DecisionTrace] = []
    risk_distribution: Dict[str, int] = Field(default_factory=lambda: {
        "factuality": 0, "privacy": 0, "bias": 0, "cost": 0
    })


class SessionInfo(BaseModel):
    """Session state visible to the dashboard."""
    session_id: str
    use_case: UseCase
    turn_count: int = 0
    current_exposure: float = 0.0
    trajectory: Trajectory = Trajectory.STABLE
    last_decision: Optional[Decision] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
