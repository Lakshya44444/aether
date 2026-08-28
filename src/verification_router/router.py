from src.models.schemas import UseCase, ActionType, RiskTier, VerificationDepth, ActionImpact
from src.risk_fabric.action_impact import get_action_profile, get_impact_class


class VerificationRouter:
    """Adaptive Verification Depth Router (Section 5.3).

    Routes on request context alone — use case tier and intended action — both known
    before any detection runs, so the routing decision costs nothing.
    """

    def route(self, use_case: UseCase, action: ActionType, risk_tier: RiskTier) -> VerificationDepth:
        impact, reversibility = get_action_profile(action)
        impact_class = get_impact_class(impact, reversibility)

        # An irreversible action always earns the deepest check available, whatever
        # the use case's own tier.
        if impact_class == "severe":
            return VerificationDepth.DEEP

        if risk_tier == RiskTier.UNACCEPTABLE:
            return VerificationDepth.DEEP

        if risk_tier == RiskTier.HIGH:
            return VerificationDepth.MEDIUM if impact_class == "routine" else VerificationDepth.DEEP

        # The fast path previously required RiskTier.MINIMAL, which no configured use
        # case declared, so the sub-200ms budget was never actually exercised. A
        # reversible, low-impact action in a limited-risk use case is exactly the
        # common case that path exists for.
        if risk_tier == RiskTier.LIMITED:
            return VerificationDepth.SHALLOW if impact_class == "routine" else VerificationDepth.MEDIUM

        if risk_tier == RiskTier.MINIMAL:
            return VerificationDepth.SHALLOW if impact_class != "severe" else VerificationDepth.DEEP

        return VerificationDepth.MEDIUM
