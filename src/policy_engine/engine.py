import os
import json
from typing import Dict, Any, Tuple
from src.models.schemas import RiskAssessment, Decision, ActionType, RiskTier
from src.risk_fabric.action_impact import get_impact_class
from src.config import config

_SEVERITY_ORDER = {
    Decision.ALLOW: 0,
    Decision.WARN: 1,
    Decision.REDACT: 2,
    Decision.ESCALATE: 3,
    Decision.BLOCK: 4,
}

_DEFAULT_THRESHOLDS = {"warn": 0.5, "block": 0.8}

_DEFAULT_POLICY = {
    "policy_id": "default",
    "fail_mode": "fail_closed",
    "mandatory_human_review_actions": [],
    "thresholds": {},
}


class PolicyEngine:
    def __init__(self, policies_dir: str):
        self.policies_dir = policies_dir
        self.policies: Dict[str, Any] = {}
        self.load_policies()

    def load_policies(self):
        """Reads JSON policy files from policies_dir."""
        if not os.path.exists(self.policies_dir):
            os.makedirs(self.policies_dir, exist_ok=True)
            return

        for filename in os.listdir(self.policies_dir):
            if filename.endswith(".json"):
                path = os.path.join(self.policies_dir, filename)
                with open(path, "r", encoding="utf-8") as f:
                    policy = json.load(f)
                    self.policies[policy.get("use_case")] = policy

    def _thresholds_for(self, policy: Dict[str, Any], category: str, impact_class: str) -> Dict[str, float]:
        """Resolves the threshold pair for a category at a given impact class.

        Thresholds are indexed by impact class rather than scaled by a multiplier, so
        every configured number stays on the detector's own [0, 1] scale and a policy
        file can be read without mentally recomputing anything.
        """
        by_category = policy.get("thresholds", {}).get(category)
        if not by_category:
            return dict(_DEFAULT_THRESHOLDS)
        if impact_class in by_category:
            return by_category[impact_class]
        # A flat {"warn": x, "block": y} block still works, applied to every action.
        if "warn" in by_category or "block" in by_category:
            return {**_DEFAULT_THRESHOLDS, **by_category}
        return dict(_DEFAULT_THRESHOLDS)

    def evaluate(self, risk_assessment: RiskAssessment) -> Tuple[Decision, str, str]:
        """Evaluates the risk assessment against the policy.

        Returns: (decision, reason, policy_id)
        """
        use_case_str = risk_assessment.use_case.value
        policy = self.policies.get(use_case_str, _DEFAULT_POLICY)

        policy_id = policy.get("policy_id", "default")
        fail_mode = policy.get("fail_mode", "fail_closed")

        try:
            impact_class = get_impact_class(
                risk_assessment.action_impact, risk_assessment.action_reversibility
            )

            worst_decision = Decision.ALLOW
            reasons = []

            # Every result is scored against policy, not just the ones a detector chose to
            # flag. `flagged` is a detector-local opinion; whether a score matters is the
            # policy's call, which is what keeps interpretation and decision separate.
            for result in risk_assessment.detection_results:
                cat_str = result.category.value
                thresholds = self._thresholds_for(policy, cat_str, impact_class)
                warn_thresh = thresholds.get("warn", 0.5)
                block_thresh = thresholds.get("block", 0.8)

                current_decision = Decision.ALLOW
                if result.score >= block_thresh:
                    current_decision = Decision.BLOCK
                elif result.score >= warn_thresh:
                    if cat_str == "privacy" and policy.get("pii_handling") == "redact":
                        current_decision = Decision.REDACT
                    else:
                        current_decision = Decision.WARN

                if _SEVERITY_ORDER[current_decision] > _SEVERITY_ORDER[worst_decision]:
                    worst_decision = current_decision

                if current_decision != Decision.ALLOW:
                    reasons.append(
                        f"{cat_str.capitalize()} score {result.score:.2f} at {impact_class} "
                        f"impact (warn {warn_thresh}, block {block_thresh}) triggered "
                        f"{current_decision.value}"
                    )

            # Actions the policy always routes past a human. REDACT is included in the
            # upgradeable set: masking a value does not make an irreversible action safe.
            mandatory_actions = policy.get("mandatory_human_review_actions", [])
            if (
                risk_assessment.action.value in mandatory_actions
                and _SEVERITY_ORDER[worst_decision] < _SEVERITY_ORDER[Decision.ESCALATE]
            ):
                worst_decision = Decision.ESCALATE
                reasons.append(
                    f"Action {risk_assessment.action.value} requires mandatory human review"
                )

            # Accumulated session exposure tightens control independently of this turn's
            # score, which is the whole point of tracking it across a conversation.
            max_exposure = policy.get("max_session_exposure", config.max_session_exposure)
            if risk_assessment.session_exposure > max_exposure:
                if _SEVERITY_ORDER[worst_decision] < _SEVERITY_ORDER[Decision.ESCALATE]:
                    worst_decision = Decision.ESCALATE
                    reasons.append(
                        f"Session exposure {risk_assessment.session_exposure:.2f} exceeds "
                        f"maximum allowed ({max_exposure})"
                    )

            # An irreversible, high-impact action carrying any live flag is never released
            # on a decision weaker than BLOCK.
            if (
                impact_class == "severe"
                and worst_decision != Decision.ALLOW
                and _SEVERITY_ORDER[worst_decision] < _SEVERITY_ORDER[Decision.BLOCK]
            ):
                worst_decision = Decision.BLOCK
                reasons.append(
                    f"Irreversible {risk_assessment.action.value} action with an active "
                    f"risk signal exceeds fail-safe threshold"
                )

            if worst_decision == Decision.ALLOW:
                return Decision.ALLOW, "All checks passed", policy_id

            return worst_decision, "; ".join(reasons), policy_id

        except Exception as e:
            if fail_mode == "fail_open":
                return Decision.ALLOW, f"Error during evaluation ({str(e)}), fail_mode is fail_open", policy_id
            return Decision.BLOCK, f"Error during evaluation ({str(e)}), fail_mode is fail_closed", policy_id
