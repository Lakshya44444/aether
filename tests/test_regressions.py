"""Regression tests for defects found by auditing the pipeline against its own claims.

Each test pins one behaviour that was previously wrong. They are written to fail if
the original defect returns, not to describe the current implementation.
"""
import asyncio
import sqlite3

import pytest

from src.config import config
from src.correction.redact import apply_redaction
from src.detectors.bias import BiasDetector
from src.detectors.cost import CostDetector
from src.detectors.factuality import FactualityDetector
from src.detectors.privacy import PrivacyDetector
from src.models.schemas import (
    ActionType, Decision, FlaggedSpan, RiskCategory, RiskTier, UseCase, VerificationDepth,
)
from src.policy_engine.engine import PolicyEngine
from src.risk_fabric.action_impact import get_action_profile, get_impact_class
from src.verification_router.router import VerificationRouter


# ── Policy thresholds ────────────────────────────────────────────────────────

def test_every_threshold_is_on_the_detector_scale():
    """Thresholds above 1.0 were unreachable, silently disabling blocking."""
    engine = PolicyEngine(config.policies_dir)
    for policy in engine.policies.values():
        for category, by_class in policy["thresholds"].items():
            for impact_class, bounds in by_class.items():
                for name, value in bounds.items():
                    assert 0.0 <= value <= 1.0, (
                        f"{policy['use_case']}.{category}.{impact_class}.{name}={value} "
                        "is outside the [0,1] range a detector score can reach"
                    )


def test_warn_never_exceeds_block():
    engine = PolicyEngine(config.policies_dir)
    for policy in engine.policies.values():
        for category, by_class in policy["thresholds"].items():
            for impact_class, bounds in by_class.items():
                assert bounds["warn"] <= bounds["block"], (
                    f"{policy['use_case']}.{category}.{impact_class}: warn above block"
                )


def test_no_use_case_is_classified_as_prohibited():
    """'unacceptable' is the AI Act's prohibited tier, not a high-risk tier."""
    engine = PolicyEngine(config.policies_dir)
    for policy in engine.policies.values():
        assert policy["risk_tier"] != RiskTier.UNACCEPTABLE.value


def test_impact_class_orders_actions_by_consequence():
    classes = {a: get_impact_class(*get_action_profile(a)) for a in ActionType}
    assert classes[ActionType.GENERATE_TEXT] == "routine"
    assert classes[ActionType.SEND_EMAIL] == "elevated"
    assert classes[ActionType.EXECUTE_PAYMENT] == "severe"


# ── Verification routing ─────────────────────────────────────────────────────

def test_shallow_path_is_reachable_for_a_configured_use_case():
    """The fast path required a tier no policy declared, so it never ran."""
    engine = PolicyEngine(config.policies_dir)
    router = VerificationRouter()
    depths = {
        router.route(UseCase(p["use_case"]), action, RiskTier(p["risk_tier"]))
        for p in engine.policies.values()
        for action in ActionType
    }
    assert VerificationDepth.SHALLOW in depths


def test_irreversible_action_always_routes_deep():
    router = VerificationRouter()
    for tier in RiskTier:
        assert router.route(
            UseCase.CUSTOMER_SUPPORT, ActionType.EXECUTE_PAYMENT, tier
        ) == VerificationDepth.DEEP


# ── Factuality ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ordinary_replies_are_not_maximum_risk():
    """Any digit, date or 'always' previously produced score 1.00."""
    detector = FactualityDetector()
    for text in [
        "Your order shipped on March 3, 2026.",
        "The refund of $49 has been processed.",
        "I can certainly help you with that.",
        "That plan costs $20 per month.",
    ]:
        result = await detector.detect("what is the status?", text)
        assert not result.flagged, f"false positive on: {text} (score {result.score})"


@pytest.mark.asyncio
async def test_attributed_claims_are_flagged():
    """Fabrications without digits previously scored 0.00."""
    detector = FactualityDetector()
    for text in [
        "Dr. Sarah Mennick personally approved your refund.",
        "According to legal, this contract is already signed.",
        "Your policy definitely covers flood damage, approved by underwriting.",
    ]:
        result = await detector.detect("what is the status?", text)
        assert result.flagged, f"false negative on: {text} (score {result.score})"


@pytest.mark.asyncio
async def test_evidence_branch_matches_whole_words():
    """'account' was judged supported by 'accountant' via substring matching."""
    detector = FactualityDetector()
    result = await detector.detect(
        "where was it closed?",
        "Your account was closed in Prague.",
        context_documents=["The accountant reviewed the filing. Headquarters is in Berlin."],
    )
    assert result.details["unsupported"] == 1


@pytest.mark.asyncio
async def test_honorific_does_not_split_a_sentence():
    from src.detectors.factuality import _split_claims
    assert _split_claims("Dr. Sarah Mennick approved it.") == ["Dr. Sarah Mennick approved it."]


@pytest.mark.asyncio
async def test_heuristic_branch_is_capped_and_labelled():
    """Without a judge model the branch must not be able to reach a block threshold."""
    detector = FactualityDetector()
    result = await detector.detect(
        "q", "Dr. X approved it, guaranteed, on March 3, 2026, worth $5 million."
    )
    assert result.branch_used == "heuristic"
    assert result.score <= result.details["ceiling"] < 0.6


# ── Privacy ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_privacy_scores_harm_not_match_count():
    """One SSN scored 0.30 while four harmless internal IPs scored 1.00."""
    detector = PrivacyDetector()
    ssn = await detector.detect("", "The applicant's SSN is 123-45-6789.")
    ips = await detector.detect("", "Nodes 10.0.0.1, 10.0.0.2, 10.0.0.3 and 10.0.0.4 are up.")
    assert ssn.score > ips.score


@pytest.mark.asyncio
async def test_private_ranges_and_versions_are_not_pii():
    detector = PrivacyDetector()
    for text in [
        "Nodes 10.0.0.1 and 192.168.1.7 are healthy.",
        "We upgraded to version 1.2.3.4 last night.",
    ]:
        assert not (await detector.detect("", text)).flagged, text


@pytest.mark.asyncio
async def test_street_suffix_does_not_match_word_tails():
    """'3 nodes last night' matched the ADDRESS pattern via the 'st' in 'last'."""
    detector = PrivacyDetector()
    for text in [
        "We upgraded 3 nodes last night.",
        "The 5 tests must pass.",
        "Order 42 will cost less.",
    ]:
        result = await detector.detect("", text)
        assert "ADDRESS" not in result.details["found_types"], text


@pytest.mark.asyncio
async def test_real_addresses_still_detected():
    detector = PrivacyDetector()
    result = await detector.detect("", "He lives at 221 Baker Street.")
    assert "ADDRESS" in result.details["found_types"]


# ── Bias ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_both_apostrophe_forms_match():
    """The rule hardcoded U+2019 and so never matched typed input."""
    detector = BiasDetector()
    assert (await detector.detect("", "That is women's work.")).flagged
    assert (await detector.detect("", "That is women’s work.")).flagged


@pytest.mark.asyncio
async def test_age_terms_need_a_person():
    detector = BiasDetector()
    for text in ["This milk is too old to use.", "The cached build is too old; rebuild it."]:
        assert not (await detector.detect("", text)).flagged, text
    assert (await detector.detect("", "The candidate is too old for this role.")).flagged


@pytest.mark.asyncio
async def test_negated_mention_is_not_bias():
    detector = BiasDetector()
    result = await detector.detect(
        "", "We should never say a candidate should be rejected because of age."
    )
    assert not result.flagged


# ── Cost ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cost_is_scoped_per_session():
    """All sessions previously shared one 'default_session' bucket."""
    detector = CostDetector()
    for _ in range(3):
        await detector.detect("a long prompt " * 50, "a long reply " * 50, session_id="tenantA")
    result = await detector.detect("hi", "hello", session_id="tenantB")
    assert result.details["session_cost_usd"] == pytest.approx(
        result.details["estimated_cost_usd"]
    )


@pytest.mark.asyncio
async def test_retry_tracking_is_bounded():
    detector = CostDetector()
    for i in range(detector.max_tracked_prompts + 200):
        await detector.detect(f"prompt {i}", "reply", session_id="s")
    assert len(detector.session_inputs["s"]) <= detector.max_tracked_prompts


@pytest.mark.asyncio
async def test_repeated_prompt_raises_cost_score():
    detector = CostDetector()
    scores = [
        (await detector.detect("same prompt", "reply", session_id="r")).score
        for _ in range(5)
    ]
    assert scores[-1] > scores[0]


# ── Redaction ────────────────────────────────────────────────────────────────

def _span(start, end, text, detail="Found PII of type EMAIL"):
    return FlaggedSpan(
        start=start, end=end, text=text,
        categories=[RiskCategory.PRIVACY], severity=1.0, detail=detail,
    )


def test_redaction_masks_every_span():
    text = "mail a@b.com or c@d.com today"
    out = apply_redaction(text, [_span(5, 12, "a@b.com"), _span(16, 23, "c@d.com")])
    assert "a@b.com" not in out and "c@d.com" not in out
    assert out.count("[EMAIL]") == 2


def test_overlapping_spans_do_not_nest():
    text = "contact 415-555-0142 now"
    out = apply_redaction(text, [
        _span(8, 20, "415-555-0142", "Found PII of type PHONE"),
        _span(8, 16, "415-555-", "Found PII of type PHONE"),
    ])
    assert out == "contact [PHONE] now"
