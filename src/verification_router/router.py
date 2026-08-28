from src.models.schemas import UseCase, ActionType, RiskTier, VerificationDepth
from src.risk_fabric.action_impact import get_action_profile
from src.models.schemas import ActionImpact

class VerificationRouter:
    """Adaptive Verification Depth Router (Section 5.3)."""
    
    def route(self, use_case: UseCase, action: ActionType, risk_tier: RiskTier) -> VerificationDepth:
        """Routes the request to the appropriate verification depth based on context."""
        impact, _ = get_action_profile(action)
        
        if risk_tier == RiskTier.UNACCEPTABLE:
            return VerificationDepth.DEEP
            
        if impact == ActionImpact.CRITICAL:
            return VerificationDepth.DEEP
            
        if risk_tier == RiskTier.HIGH or impact == ActionImpact.HIGH:
            return VerificationDepth.DEEP
            
        if risk_tier == RiskTier.LIMITED:
            return VerificationDepth.MEDIUM
            
        if risk_tier == RiskTier.MINIMAL and action in (ActionType.GENERATE_TEXT, ActionType.DRAFT_EMAIL):
            return VerificationDepth.SHALLOW
            
        return VerificationDepth.MEDIUM
