import re
import time
from typing import Any

from src.models.schemas import DetectionResult, FlaggedSpan, RiskCategory
from src.detectors.base import BaseDetector

# Matches a straight or curly apostrophe. Hardcoding U+2019 made the rule unmatchable
# against ordinary typed input.
_APOS = r"['’]"

# Age and appearance terms only count as bias when aimed at a person. Without a target,
# "this milk is too old" and "the cached build is too old" both scored as age bias.
_PERSON = (
    r'(?:he|she|they|him|her|them|his|their|candidate|applicant|employee|worker|'
    r'manager|customer|client|person|man|woman|guy|lady|hire|staff|team member)'
)

_PATTERNS = {
    "stereotyping": [
        r'\b(?:all|most|typical|every)\s+(?:men|women|blacks?|whites?|asians?|hispanics?|'
        r'gays?|straights?|immigrants?|muslims?|christians?|jews?)\s+(?:are|is|tend|always|never)\b',
        r'\btypical\s+(?:male|female|man|woman)\b',
    ],
    "consequential": [
        r'\b(?:should|must)\s+be\s+(?:rejected|denied|fired|terminated|excluded|passed over)\b',
        r'\b(?:is|are)\s+not\s+qualified\b',
        r'\b(?:is|are)\s+incompetent\b',
    ],
    "gender_bias": [
        rf'\bman\s+up\b',
        rf'\bact\s+like\s+a\s+man\b',
        rf'\bwomen{_APOS}?s?\s+work\b',
    ],
    "age_bias": [
        rf'\b{_PERSON}\s+(?:is|are|was|were|seems?|looks?)\s+(?:too\s+old|too\s+young|past\s+(?:his|her|their)\s+prime)\b',
        rf'\b(?:too\s+old|too\s+young)\s+(?:for\s+(?:the|this|that)\s+(?:role|job|position|team)|to\s+(?:be\s+)?(?:hired?|promoted?))\b',
    ],
}

_SEVERITY = {
    "consequential": 0.8,
    "stereotyping": 0.6,
    "gender_bias": 0.5,
    "age_bias": 0.5,
}

# A negated or quoted mention is a discussion of bias, not an instance of it.
_NEGATION = re.compile(
    r'\b(?:never|not|don’t|dont|do not|avoid|shouldn’t|shouldnt|should not|'
    r'must not|refrain from)\b[^.!?]{0,40}$',
    re.IGNORECASE,
)


class BiasDetector(BaseDetector):
    """Bias and toxicity detector."""

    @property
    def category(self) -> str:
        return RiskCategory.BIAS

    async def detect(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
        start_time = time.time()

        flagged_spans = []
        max_score = 0.0

        for bias_type, regex_list in _PATTERNS.items():
            for pattern in regex_list:
                for match in re.finditer(pattern, output_text, re.IGNORECASE):
                    if _NEGATION.search(output_text[: match.start()]):
                        continue

                    severity = _SEVERITY[bias_type]
                    max_score = max(max_score, severity)

                    flagged_spans.append(FlaggedSpan(
                        start=match.start(),
                        end=match.end(),
                        text=match.group(),
                        categories=[RiskCategory.BIAS],
                        severity=severity,
                        detail=f"Detected {bias_type} language pattern."
                    ))

        latency_ms = (time.time() - start_time) * 1000

        return DetectionResult(
            category=RiskCategory.BIAS,
            score=max_score,
            flagged=max_score > 0,
            details={"matches": len(flagged_spans)},
            flagged_spans=flagged_spans,
            latency_ms=latency_ms
        )
