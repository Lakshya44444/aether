from typing import List, Optional, Tuple
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from src.models.schemas import UseCase, DetectionResult, Decision, Trajectory, SessionInfo
from src.state import StateStore, MemoryStore
from src.config import config


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionState(BaseModel):
    session_id: str
    use_case: UseCase
    turn_count: int = 0
    current_exposure: float = 0.0
    trajectory: Trajectory = Trajectory.STABLE
    risk_history: List[float] = Field(default_factory=list)
    last_decision: Optional[Decision] = None
    created_at: datetime = Field(default_factory=_utcnow)
    last_seen: datetime = Field(default_factory=_utcnow)


class SessionTracker:
    """Risk carried across the turns of one conversation.

    State lives in a `StateStore` rather than a dict on this object. As a dict it made
    the gateway single-process by construction -- a second worker got its own copy, so
    turn 2 could land somewhere that never saw turn 1 and `max_session_exposure`,
    trajectory and retry counting silently stopped firing. With Redis behind the store
    every worker reads the same session.

    The store's TTL also replaces the `_evict_stale` sweep this class used to run on
    every update: an expiring key is what "drop idle sessions" means, and it does not
    need a scan.
    """

    def __init__(self, store: Optional[StateStore] = None):
        # `store or MemoryStore()` looked equivalent and was not: MemoryStore
        # defines __len__, so an empty one is falsy and the shared store passed in
        # here was silently swapped for a private one. Every component then had its
        # own view of every session.
        self.store = MemoryStore() if store is None else store
        self.trajectory_window = max(2, config.trajectory_window_turns)

    @property
    def _ttl(self) -> int:
        return config.session_timeout_minutes * 60

    @staticmethod
    def _key(session_id: str) -> str:
        return f"session:{session_id}"

    async def update(
        self, session_id: str, use_case: UseCase, detection_results: List[DetectionResult]
    ) -> Tuple[float, float, Trajectory]:
        """Updates session state and returns (current_turn_risk, session_exposure, trajectory).

        Session exposure is intentionally treated as a governance heuristic rather than a calibrated probability.
        It is damped so a single risky turn does not immediately dominate a low-risk workflow, while repeated
        risky turns gradually push a session into a stricter control regime.
        """
        # current_turn_risk: max of all detection scores for this turn
        current_turn_risk = 0.0
        if detection_results:
            current_turn_risk = max([dr.score for dr in detection_results] + [0.0])

        window = self.trajectory_window

        def mutate(raw: Optional[dict]) -> dict:
            session = (
                SessionState.model_validate(raw) if raw
                else SessionState(session_id=session_id, use_case=use_case)
            )
            session.last_seen = _utcnow()
            session.risk_history.append(current_turn_risk)
            del session.risk_history[: -2 * window]
            session.turn_count += 1

            # Damped governance heuristic: a single high-risk turn should raise exposure, but not instantly
            # exhaust a low-stakes use case. This preserves the report's design intent without turning the
            # very first turn into an immediate block in the demo.
            session.current_exposure = min(
                1.0,
                (session.current_exposure * config.exposure_decay)
                + (current_turn_risk * config.exposure_turn_weight),
            )

            # Trajectory over a sliding window.
            trajectory = Trajectory.STABLE
            if len(session.risk_history) >= 2 * window:
                recent = session.risk_history[-window:]
                prev = session.risk_history[-2 * window:-window]
                avg_recent = sum(recent) / window
                avg_prev = sum(prev) / window
                if avg_recent > avg_prev + config.trajectory_delta:
                    trajectory = Trajectory.RISING
                elif avg_recent < avg_prev - config.trajectory_delta:
                    trajectory = Trajectory.FALLING
            session.trajectory = trajectory
            return session.model_dump(mode="json")

        updated = await self.store.read_modify_write(self._key(session_id), self._ttl, mutate)
        session = SessionState.model_validate(updated)
        return current_turn_risk, session.current_exposure, session.trajectory

    async def record_decision(self, session_id: str, decision: Decision) -> None:
        """Stores the outcome so the dashboard can show what a session actually got.

        `SessionInfo.last_decision` was declared and never populated.
        """
        raw = await self.store.get(self._key(session_id))
        if raw is None:
            return
        raw["last_decision"] = decision.value
        await self.store.put(self._key(session_id), raw, self._ttl)

    async def get_session_info(self, session_id: str) -> SessionInfo:
        """Returns session info for the dashboard."""
        raw = await self.store.get(self._key(session_id))
        if raw is None:
            # An unknown or expired session, reported as empty rather than as an error.
            return SessionInfo(session_id=session_id, use_case=UseCase.CUSTOMER_SUPPORT)

        s = SessionState.model_validate(raw)
        return SessionInfo(
            session_id=s.session_id,
            use_case=s.use_case,
            turn_count=s.turn_count,
            current_exposure=s.current_exposure,
            trajectory=s.trajectory,
            last_decision=s.last_decision,
            created_at=s.created_at,
        )

    async def forget(self, session_id: str) -> None:
        await self.store.delete(self._key(session_id))
