"""Judge-model client used by the factuality detector.

Kept deliberately small: one OpenAI-compatible chat call. The judge is always a
separate, cheaper model from the one that produced the answer under review, so the
detector never asks a model to grade its own output.
"""
import asyncio
from typing import List, Optional

import httpx

from src.config import config


class JudgeUnavailable(RuntimeError):
    """Raised when no judge model is configured or the call fails."""


def judge_configured() -> bool:
    return bool(config.llm_api_key) and not config.demo_mode


async def _complete(prompt: str, temperature: float, timeout: float) -> str:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{config.llm_api_base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.llm_api_key}"},
            json={
                "model": config.judge_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": config.judge_max_tokens,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


async def sample_answers(question: str, n: int, timeout: float) -> List[str]:
    """Draws n independent answers to the same question, for consistency checking.

    This is the SelfCheckGPT premise: a model that knows an answer restates it
    consistently, while a confabulated answer varies across samples.
    """
    if not judge_configured():
        raise JudgeUnavailable("no judge model configured")
    prompt = f"Answer concisely and factually.\n\nQuestion: {question}\nAnswer:"
    results = await asyncio.gather(
        *[_complete(prompt, temperature=1.0, timeout=timeout) for _ in range(n)],
        return_exceptions=True,
    )
    answers = [r for r in results if isinstance(r, str)]
    if not answers:
        raise JudgeUnavailable("all judge samples failed")
    return answers


async def entails(claim: str, reference: str, timeout: float) -> Optional[bool]:
    """Asks the judge whether `reference` supports `claim`.

    Returns True/False, or None when the judge gives an answer that is neither.
    """
    if not judge_configured():
        raise JudgeUnavailable("no judge model configured")
    prompt = (
        "Does the reference text support the claim? "
        "Reply with exactly one word: SUPPORTED, CONTRADICTED, or UNSUPPORTED.\n\n"
        f"Reference: {reference}\n\nClaim: {claim}\n\nAnswer:"
    )
    verdict = (await _complete(prompt, temperature=0.0, timeout=timeout)).upper()
    if "CONTRADICT" in verdict:
        return False
    if "SUPPORT" in verdict:
        return True
    return None
