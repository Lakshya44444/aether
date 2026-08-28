from typing import Tuple
from src.models.schemas import ActionType, ActionImpact, ActionReversibility

# Action Impact × Reversibility lookup table from Section 5.4.2
_ACTION_PROFILE_MAP = {
    ActionType.GENERATE_TEXT: (ActionImpact.LOW, ActionReversibility.HIGH),
    ActionType.DRAFT_EMAIL: (ActionImpact.LOW, ActionReversibility.HIGH),
    ActionType.SEND_EMAIL: (ActionImpact.MEDIUM, ActionReversibility.MEDIUM),
    ActionType.UPDATE_CRM: (ActionImpact.MEDIUM, ActionReversibility.MEDIUM),
    ActionType.DELETE_RECORD: (ActionImpact.HIGH, ActionReversibility.LOW),
    ActionType.EXECUTE_PAYMENT: (ActionImpact.CRITICAL, ActionReversibility.VERY_LOW),
}

_IMPACT_SCORE = {
    ActionImpact.LOW: 1,
    ActionImpact.MEDIUM: 2,
    ActionImpact.HIGH: 3,
    ActionImpact.CRITICAL: 4,
}

_REVERSIBILITY_SCORE = {
    ActionReversibility.HIGH: 1,
    ActionReversibility.MEDIUM: 2,
    ActionReversibility.LOW: 3,
    ActionReversibility.VERY_LOW: 4,
}

def get_action_profile(action: ActionType) -> Tuple[ActionImpact, ActionReversibility]:
    """Returns the (Impact, Reversibility) profile for a given action."""
    return _ACTION_PROFILE_MAP.get(action, (ActionImpact.LOW, ActionReversibility.HIGH))

def compute_action_risk_multiplier(impact: ActionImpact, reversibility: ActionReversibility) -> float:
    """Computes a risk multiplier based on impact and reversibility (1.0 to 2.5)."""
    i_score = _IMPACT_SCORE.get(impact, 1)
    r_score = _REVERSIBILITY_SCORE.get(reversibility, 1)
    
    total_score = i_score + r_score  # ranges from 2 to 8
    
    # Map [2, 8] to [1.0, 2.5]
    multiplier = 1.0 + (total_score - 2) * 0.25
    return multiplier
