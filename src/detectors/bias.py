import re
import time
from typing import Any

from src.models.schemas import DetectionResult, FlaggedSpan, RiskCategory
from src.detectors.base import BaseDetector, near
from src.config import config

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
    # Bias that never names the protected trait, matched as a group term near a
    # generalising frame rather than as fixed phrases -- see _GROUP / _FRAME below.
    # These carry the same hiring consequence as the explicit patterns above and were
    # the largest miss class: none of them contain a demographic word at all.
    "coded_proxy": [
        # Praise conditioned on group membership -- "articulate for someone from ...".
        r'\b(?:surprisingly|unusually|remarkably)\s+\w+(?:\s+\w+)?\s+for\s+'
        r'(?:a|an|someone|somebody|his|her|their)\b',
        r'\b(?:articulate|well[-\s]spoken|eloquent|intelligent|competent)\s+for\s+'
        r'(?:a|an|someone|somebody)\b',
        # "Culture fit" as the stated reason for a rejection.
        r'\b(?:not|isn\'t|is\s+not|aren\'t|are\s+not)\s+(?:really\s+)?(?:a\s+)?culture\s+fit\b',
    ],
    "age_bias": [
        rf'\b{_PERSON}\s+(?:is|are|was|were|seems?|looks?)\s+(?:too\s+old|too\s+young|past\s+(?:his|her|their)\s+prime)\b',
        rf'\b(?:too\s+old|too\s+young)\s+(?:for\s+(?:the|this|that)\s+(?:role|job|position|team)|to\s+(?:be\s+)?(?:hired?|promoted?))\b',
    ],
}

_SEVERITY = {
    "consequential": 0.8,
    "stereotyping": 0.6,
    "coded_proxy": 0.6,
    "gender_bias": 0.5,
    "age_bias": 0.5,
}

# ── coded-proxy slots ────────────────────────────────────────────────────────
# The proxy families all have the same shape: a group referred to without naming the
# protected trait, next to a frame that generalises the whole group into a deficiency.
# Enumerating the sentences misses every paraphrase; matching the two halves separately
# and requiring them to sit close together covers the family. Both halves are required,
# which is what keeps "the team member is too junior for this role" out of it.
_GROUP = re.compile(
    r'\b(?:older|younger|elderly|young|aging|ageing|senior|junior|veteran|mature|'
    r'fresh|recent|new)\s+'
    r'(?:candidates?|applicants?|workers?|employees?|people|staff|graduates?|hires?|'
    r'folks|engineers?|developers?|managers?)\b'
    r'|\b(?:working\s+)?(?:mothers?|fathers?|parents?|caregivers?|carers?|'
    r'pregnant\s+\w+|immigrants?|foreigners?)\b'
    r'|\bat\s+(?:that|this|his|her|their|a\s+certain)\s+(?:stage\s+of\s+(?:life|career)|'
    r'point\s+in\s+(?:life|their\s+career)|age)\b'
    r'|\bof\s+(?:that|a\s+certain)\s+age\b'
    r'|\bdigital\s+natives?\b'
    r'|\b(?:candidates?|applicants?|people|students?|graduates?|they|someone)\s+'
    r'(?:from|out\s+of)\s+(?:that|those|this|these)\s+\w+\b',
    re.IGNORECASE,
)

# A universal quantifier over a protected class. On its own this is not bias -- "all
# women in the study responded" is a count -- so it is scored only next to a frame.
_QUANTIFIED = re.compile(
    r'\b(?:all|most|every|any|typical)\s+(?:the\s+)?'
    r'(?:m[ae]n|wom[ae]n|blacks?|whites?|asians?|hispanics?|latinos?|gays?|straights?|'
    r'immigrants?|foreigners?|muslims?|christians?|jews?|hindus?|'
    r'millennials?|boomers?|mothers?|fathers?)\b',
    re.IGNORECASE,
)

# A frame that turns an individual judgement into a statement about the whole group.
_FRAME = re.compile(
    r'\b(?:cannot|can\s?not|can\'t|lack|lacks|struggles?|rarely|seldom|'
    r'tends?\s+to|typically|usually|generally|often|always|never|'
    r'(?:is|are|was|were)\s+(?:less|not|rarely|weaker|slower|typically|usually)|'
    r'(?:isn\'t|aren\'t|don\'t|doesn\'t)|'
    r'less\s+likely|more\s+(?:energy|drive|stamina|hunger)\s+than|'
    r'set\s+in\s+(?:his|her|their)\s+ways|resistant\s+to\s+change|'
    r'weaker|slower|behind\s+the\s+curve|not\s+a\s+fit|'
    # generic-predicate deficiencies: the claim is about the group's outcomes
    r'burns?\s+out|burn\s+out|wash\s+out|drop\s+out|quits?|underperform\w*|'
    r'underprepared|underqualified|fails?)\b',
    re.IGNORECASE,
)

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

    def scan(self, input_text: str, output_text: str, **kwargs: Any) -> DetectionResult:
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

        # Coded proxies: a group term sitting near a generalising frame, in either
        # order -- "older candidates rarely adapt" and "more energy than a candidate at
        # that stage of life" are the same claim with the halves swapped.
        for group in (_GROUP, _QUANTIFIED):
            for start, end in near(output_text, group, _FRAME, window=40, ordered=False):
                if _NEGATION.search(output_text[:start]):
                    continue
                severity = _SEVERITY["coded_proxy"]
                max_score = max(max_score, severity)
                flagged_spans.append(FlaggedSpan(
                    start=start, end=end, text=output_text[start:end],
                    categories=[RiskCategory.BIAS], severity=severity,
                    detail="Detected coded_proxy language pattern."))

        latency_ms = (time.time() - start_time) * 1000

        return DetectionResult(
            category=RiskCategory.BIAS,
            score=max_score,
            flagged=max_score >= config.bias_threshold,
            details={"matches": len(flagged_spans)},
            flagged_spans=flagged_spans,
            latency_ms=latency_ms
        )
