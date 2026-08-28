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

# Impact and reversibility are combined into a coarse class rather than a numeric
# multiplier. A multiplier scaled a [0,1] detection score onto an unbounded range,
# which put policy thresholds on a scale nobody could reason about; a class selects
# which threshold set applies and leaves the score itself untouched.
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

# Threshold classes, ordered least to most consequential.
IMPACT_CLASSES = ("routine", "elevated", "severe")


def get_action_profile(action: ActionType) -> Tuple[ActionImpact, ActionReversibility]:
    """Returns the (Impact, Reversibility) profile for a given action."""
    return _ACTION_PROFILE_MAP.get(action, (ActionImpact.LOW, ActionReversibility.HIGH))


def get_impact_class(impact: ActionImpact, reversibility: ActionReversibility) -> str:
    """Maps an action profile onto the threshold class its policy should use.

    routine  — reversible, low consequence (generate_text, draft_email)
    elevated — recoverable but externally visible (send_email, update_crm)
    severe   — irreversible or critical (delete_record, execute_payment)
    """
    combined = _IMPACT_SCORE.get(impact, 1) + _REVERSIBILITY_SCORE.get(reversibility, 1)
    if combined <= 3:
        return "routine"
    if combined <= 5:
        return "elevated"
    return "severe"
