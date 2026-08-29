import re
import time
from typing import Any

from src.models.schemas import DetectionResult, FlaggedSpan, RiskCategory
from src.detectors.base import BaseDetector
from src.config import config

# Prompt injection is the one attack class that is specific to running a model at all:
# instructions arriving as data. These are the published families, not an attempt at
# coverage -- a determined attacker rephrases past regexes, which is why the score is
# capped below any policy's block threshold and this detector escalates rather than
# decides on its own.
_PATTERNS = {
    "instruction_override": (
        0.7,
        r'\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+|your\s+|previous\s+|prior\s+|above\s+)*'
        r'(?:instructions?|prompts?|rules?|directions?|guidelines?|context)\b',
    ),
    "role_reassignment": (
        0.6,
        r'\byou\s+are\s+now\s+(?:a|an|the)\b|\bpretend\s+(?:to\s+be|you\s+are)\b|'
        r'\bact\s+as\s+(?:if\s+you\s+are\s+)?(?:a|an|the)\s+(?:unrestricted|uncensored|unfiltered|jailbroken)\b|'
        r'\b(?:enable|enter|activate)\s+(?:DAN|developer|god|debug)\s+mode\b',
    ),
    "system_prompt_exfiltration": (
        0.6,
        r'\b(?:reveal|show|print|repeat|output|tell\s+me)\s+(?:me\s+)?(?:your|the)\s+'
        r'(?:system\s+prompt|initial\s+instructions?|original\s+instructions?|rules?|configuration)\b|'
        r'\brepeat\s+(?:everything|the\s+text)\s+above\b',
    ),
    "guardrail_evasion": (
        0.65,
        r'\b(?:bypass|circumvent|get\s+around|disable|turn\s+off)\s+(?:the\s+|your\s+|all\s+)?'
        r'(?:safety|guardrails?|filters?|restrictions?|content\s+polic\w+|moderation)\b|'
        r'\bwithout\s+(?:any\s+)?(?:restrictions?|filters?|censorship|limitations?)\b',
    ),
    "delimiter_smuggling": (
        0.5,
        r'(?:^|\n)\s*(?:###\s*)?(?:system|assistant)\s*[:>]|<\|(?:im_start|im_end|system)\|>|'
        r'\[\s*(?:SYSTEM|INST)\s*\]',
    ),
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
        for family, (severity, pattern) in _PATTERNS.items():
            for match in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
                families.add(family)
                score = max(score, severity)
                spans.append(FlaggedSpan(
                    start=match.start(),
                    end=match.end(),
                    text=match.group(),
                    categories=[RiskCategory.INJECTION],
                    severity=severity,
                    detail=f"Detected {family} injection pattern.",
                ))

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
