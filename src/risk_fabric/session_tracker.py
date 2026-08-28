from typing import Dict, List, Tuple
from datetime import datetime
from pydantic import BaseModel, Field

from src.models.schemas import UseCase, DetectionResult, Trajectory, SessionInfo
from src.config import config

class SessionState(BaseModel):
    session_id: str
    use_case: UseCase
    turn_count: int = 0
    current_exposure: float = 0.0
    trajectory: Trajectory = Trajectory.STABLE
    risk_history: List[float] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class SessionTracker:
    def __init__(self):
        self.sessions: Dict[str, SessionState] = {}
        self.trajectory_window = config.trajectory_window_turns

    def update(self, session_id: str, use_case: UseCase, detection_results: List[DetectionResult]) -> Tuple[float, float, Trajectory]:
        """Updates session state and returns (current_turn_risk, session_exposure, trajectory).

        Session exposure is intentionally treated as a governance heuristic rather than a calibrated probability.
        It is damped so a single risky turn does not immediately dominate a low-risk workflow, while repeated
        risky turns gradually push a session into a stricter control regime.
        """
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState(session_id=session_id, use_case=use_case)

        session = self.sessions[session_id]

        # current_turn_risk: max of all detection scores for this turn
        current_turn_risk = 0.0
        if detection_results:
            current_turn_risk = max([dr.score for dr in detection_results] + [0.0])

        session.risk_history.append(current_turn_risk)
        session.turn_count += 1

        # Damped governance heuristic: a single high-risk turn should raise exposure, but not instantly exhaust
        # a low-stakes use case. This preserves the report's design intent without turning the very first turn
        # into an immediate block in the demo.
        prior_exposure = session.current_exposure
        damped_turn = current_turn_risk * 0.6
        session.current_exposure = min(1.0, (prior_exposure * 0.55) + damped_turn)

        # compute trajectory using a sliding window
        trajectory = Trajectory.STABLE

        if len(session.risk_history) >= 6:
            recent_3 = session.risk_history[-3:]
            prev_3 = session.risk_history[-6:-3]
            avg_recent = sum(recent_3) / 3.0
            avg_prev = sum(prev_3) / 3.0

            if avg_recent > avg_prev + 0.05:
                trajectory = Trajectory.RISING
            elif avg_recent < avg_prev - 0.05:
                trajectory = Trajectory.FALLING

        session.trajectory = trajectory

        return current_turn_risk, session.current_exposure, trajectory

    def get_session_info(self, session_id: str) -> SessionInfo:
        """Returns session info for the dashboard."""
        if session_id not in self.sessions:
            # Return empty dummy if not found
            return SessionInfo(session_id=session_id, use_case=UseCase.CUSTOMER_SUPPORT)
        
        s = self.sessions[session_id]
        return SessionInfo(
            session_id=s.session_id,
            use_case=s.use_case,
            turn_count=s.turn_count,
            current_exposure=s.current_exposure,
            trajectory=s.trajectory,
            created_at=s.created_at
        )
