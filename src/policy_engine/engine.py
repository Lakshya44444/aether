import os
import json
from typing import Dict, Any, Tuple
from src.models.schemas import RiskAssessment, Decision, ActionType, RiskTier
from src.risk_fabric.action_impact import compute_action_risk_multiplier

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

    def evaluate(self, risk_assessment: RiskAssessment) -> Tuple[Decision, str, str]:
        """Evaluates the risk assessment against the policy.
        Returns: (decision, reason, policy_id)
        """
        use_case_str = risk_assessment.use_case.value
        policy = self.policies.get(use_case_str)

        # Default policy if none matched
        if not policy:
            policy = {
                "policy_id": "default",
                "fail_mode": "fail_closed",
                "mandatory_human_review_actions": [],
                "max_session_exposure": 1.0,
                "thresholds": {}
            }

        policy_id = policy.get("policy_id", "default")
        fail_mode = policy.get("fail_mode", "fail_closed")

        try:
            # Decision logic
            multiplier = compute_action_risk_multiplier(risk_assessment.action_impact, risk_assessment.action_reversibility)

            # First, evaluate the actual risk signal and action impact. This keeps the decision aligned with the
            # report's central claim: what the AI is about to do matters as much as what it said.
            max_exposure = policy.get("max_session_exposure", 1.0)
            exposure_trigger = risk_assessment.session_exposure > max_exposure and risk_assessment.current_turn_risk >= 0.7

            # 3. Check detection results
            worst_decision = Decision.ALLOW
            reasons = []
            severity_order = {
                Decision.ALLOW: 0,
                Decision.WARN: 1,
                Decision.REDACT: 2,
                Decision.ESCALATE: 3,
                Decision.BLOCK: 4
            }

            for result in risk_assessment.detection_results:
                if result.flagged:
                    cat_str = result.category.value
                    thresholds = policy.get("thresholds", {}).get(cat_str, {"warn": 0.5, "block": 0.8})

                    adjusted_score = result.score * multiplier
                    warn_thresh = thresholds.get("warn", 0.5)
                    block_thresh = thresholds.get("block", 0.8)

                    current_decision = Decision.ALLOW
                    if adjusted_score >= block_thresh:
                        current_decision = Decision.BLOCK
                    elif adjusted_score >= warn_thresh:
                        if cat_str == "privacy" and policy.get("pii_handling") == "redact":
                            current_decision = Decision.REDACT
                        else:
                            current_decision = Decision.WARN

                    if severity_order[current_decision] > severity_order[worst_decision]:
                        worst_decision = current_decision

                    if current_decision != Decision.ALLOW:
                        reasons.append(f"{cat_str.capitalize()} score {adjusted_score:.2f} (adj) triggered {current_decision.value}")

            # Escalation is a policy-driven requirement for certain actions, but it should not override a more severe
            # action-impact-driven BLOCK when the underlying risk is already high.
            mandatory_actions = policy.get("mandatory_human_review_actions", [])
            if risk_assessment.action.value in mandatory_actions and worst_decision in (Decision.ALLOW, Decision.WARN):
                worst_decision = Decision.ESCALATE
                reasons.append(f"Action {risk_assessment.action.value} requires mandatory human review")

            if exposure_trigger:
                if severity_order[Decision.BLOCK] >= severity_order[worst_decision]:
                    worst_decision = Decision.BLOCK
                    reasons.append(f"Session exposure {risk_assessment.session_exposure:.2f} exceeds maximum allowed ({max_exposure})")

            # If the action is irreversible and critical, a high-risk decision should be BLOCK even when a policy may
            # only propose escalation for the action itself.
            if risk_assessment.action_impact.value in {"critical", "high"} and risk_assessment.action_reversibility.value in {"low", "very_low"} and worst_decision in {Decision.ESCALATE, Decision.WARN}:
                worst_decision = Decision.BLOCK
                reasons.append(f"Irreversible {risk_assessment.action.value} action with high impact exceeds fail-safe threshold")

            if worst_decision == Decision.ALLOW:
                return Decision.ALLOW, "All checks passed", policy_id

            return worst_decision, "; ".join(reasons), policy_id

        except Exception as e:
            if fail_mode == "fail_open":
                return Decision.ALLOW, f"Error during evaluation ({str(e)}), fail_mode is fail_open", policy_id
            else:
                return Decision.BLOCK, f"Error during evaluation ({str(e)}), fail_mode is fail_closed", policy_id
