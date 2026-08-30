import re
import time
from typing import Any

from src.models.schemas import DetectionResult, FlaggedSpan, RiskCategory
from src.detectors.base import BaseDetector, near
from src.config import config

# Prompt injection is the one attack class specific to running a model at all:
# instructions arriving as data. The families below are the published ones, matched as
# a verb near its object rather than as a fixed phrase -- an attacker rephrases past a
# literal pattern almost for free, and the whole family shares one verb slot and one
# object slot. Score is still capped below any policy's block threshold: a match is
# evidence of an attempt, never proof of a successful one, so this detector escalates
# rather than deciding on its own.


def _re(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


# ── slot lexicons ────────────────────────────────────────────────────────────
# Cancelling something is the act; what is cancelled decides whether it is an attack.
# "Ignore the typo in my last message" shares the verb with "ignore all previous
# instructions" and differs only in the object, which is why both slots are required.
_CANCEL = _re(
    r"\b(?:ignor\w*|disregard\w*|forget|override|overrid\w+|discard\w*|dismiss\w*|"
    r"drop|erase|wipe|clear|skip|abandon|unlearn|"
    r"set\s+aside|put\s+aside|throw\s+out|leave\s+out|pay\s+no\s+attention\s+to|"
    r"stop\s+following|no\s+longer\s+follow|don'?t\s+follow|never\s+mind)\b"
)
_INSTRUCTION = _re(
    r"\b(?:instructions?|prompts?|rules?|directions?|guidelines?|guidance|context|"
    r"polic(?:y|ies)|constraints?|orders?|briefing|training|programming|"
    r"system\s+(?:message|prompt)|"
    r"(?:everything|anything|all|whatever)\s+(?:that\s+)?(?:you\s+(?:were|was|have\s+been)\s+)?"
    r"(?:told|given|said|instructed|above|before|earlier|previously)|"
    r"what\s+you\s+(?:were|was)\s+(?:told|given|instructed)|"
    r"the\s+(?:above|preceding|foregoing)\b)"
)

_EVADE = _re(
    r"\b(?:bypass\w*|circumvent\w*|get\s+around|work\s+around|go\s+around|"
    r"disabl\w+|turn(?:ed)?\s+off|switch(?:ed)?\s+off|shut\s+off|evade|sidestep|"
    r"lift|relax|loosen|remove|suspend|skip|drop|without)\b"
)
_SAFETY = _re(
    r"\b(?:safety|safeguards?|guard\s?rails?|filters?|filtering|restrictions?|"
    r"limitations?|censorship|moderation|protections?|"
    r"content\s+polic\w+|safety\s+(?:rules?|checks?|layer)|"
    r"(?:usage|content)\s+(?:rules?|guidelines?))\b"
)

_REVEAL = _re(
    r"\b(?:reveal|show|print|repeat|output|display|echo|recite|disclose|dump|paste|"
    r"list|spit\s+out|read\s+back|tell\s+me|give\s+me|share|what\s+(?:are|were|is|was))\b"
)
_SECRET = _re(
    r"\b(?:system\s+prompt|initial\s+instructions?|original\s+instructions?|"
    r"your\s+(?:instructions?|rules?|configuration|config|setup|guidelines?|"
    r"prompt|directive[s]?)|"
    r"(?:everything|the\s+(?:text|message|content))(?:\s+\w+){0,4}\s+above|"
    r"your\s+first\s+message)\b"
)

# Standalone shapes: no verb/object pair, the phrase itself is the whole signal.
_STANDALONE = {
    "role_reassignment": (0.6, _re(
        r"\byou\s+are\s+now\s+(?:a|an|the)\b|"
        r"\bfrom\s+(?:now|here|this\s+point)\s+on[, ]+you\s+(?:are|will\s+be|must\s+be)\b|"
        r"\byou\s+are\s+no\s+longer\s+(?:a|an|the|bound|required)\b|"
        r"\bpretend\s+(?:to\s+be|you\s+are|that\s+you)\b|"
        r"\brole[\s-]?play\s+as\b|\bimagine\s+(?:you\s+are|that\s+you)\b|"
        r"\bassume\s+the\s+(?:role|persona|identity)\s+of\b|"
        r"\byour\s+new\s+(?:role|persona|identity|instructions?)\s+(?:is|are)\b|"
        r"\bact\s+as\s+(?:if\s+you\s+are\s+)?(?:a|an|the)\s+"
        r"(?:unrestricted|uncensored|unfiltered|jailbroken|amoral)\b|"
        r"\b(?:enable|enter|activate|switch\s+to|go\s+into|you\s+are\s+in)\s+"
        r"(?:the\s+)?(?:DAN|developer|dev|god|debug|admin|root|sudo|"
        r"unrestricted|unfiltered|jailbreak)\s+mode\b"
    )),
    "guardrail_evasion": (0.65, _re(
        r"\b(?:unfiltered|uncensored|unrestricted|jailbroken|jailbreak)\b|"
        r"\bno\s+(?:filter|filters|restrictions?|rules?|limits?|guardrails?|"
        r"content\s+polic(?:y|ies)|safety\s+\w+)\b|"
        r"\bwithout\s+(?:any\s+)?(?:restrictions?|filters?|censorship|limitations?|"
        r"guardrails?|safeguards?|rules?)\b"
    )),
    "delimiter_smuggling": (0.5, _re(
        r"(?:^|\n)\s*(?:###\s*|---\s*)?(?:system|assistant)\s*[:>]|"
        r"<\|(?:im_start|im_end|system)\|>|</?system>|\{\{\s*system\s*\}\}|"
        r"\[\s*(?:SYSTEM|INST)\s*\]"
    )),
}

# Verb-slot near object-slot. Each entry is (severity, verb, object, ordered).
_PAIRED = {
    "instruction_override": (0.7, _CANCEL, _INSTRUCTION, True),
    "guardrail_evasion": (0.65, _EVADE, _SAFETY, False),
    "system_prompt_exfiltration": (0.6, _REVEAL, _SECRET, True),
}

# A pattern match is evidence of an attempt, never proof of a successful one. Capping
# below every configured block threshold keeps this detector on the escalate/deny-review
# path rather than letting a regex block traffic outright.
_CEILING = config.injection_ceiling


class InjectionDetector(BaseDetector):
    """Prompt-injection and jailbreak screening for the input side."""

    @property
    def category(self) -> str:
        return RiskCategory.INJECTION

    def scan(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        start_time = time.time()
        # Screens the prompt, not the completion: this is an input-side control.
        text = input_text or output_text

        spans = []
        score = 0.0
        families = set()

        def hit(family: str, severity: float, start: int, end: int) -> None:
            nonlocal score
            families.add(family)
            score = max(score, severity)
            spans.append(FlaggedSpan(
                start=start, end=end, text=text[start:end],
                categories=[RiskCategory.INJECTION], severity=severity,
                detail=f"Detected {family} injection pattern.",
            ))

        for family, (severity, verb, obj, ordered) in _PAIRED.items():
            for start, end in near(text, verb, obj, ordered=ordered):
                hit(family, severity, start, end)

        for family, (severity, pattern) in _STANDALONE.items():
            for match in pattern.finditer(text):
                hit(family, severity, match.start(), match.end())

        # Several distinct families in one prompt is a constructed attack, not a phrase
        # that happened to look like one.
        if len(families) > 1:
            score = min(_CEILING, score + 0.1 * (len(families) - 1))

        return DetectionResult(
            category=RiskCategory.INJECTION,
            score=score,
            flagged=score >= config.injection_threshold,
            flagged_spans=spans,
            details={"families": sorted(families), "count": len(spans), "ceiling": _CEILING},
            latency_ms=(time.time() - start_time) * 1000,
        )
