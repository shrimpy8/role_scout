"""Cross-model LLM judge for eval quality assessment.

Uses a non-Anthropic model (OpenAI GPT-4o or Gemini 2.5 Flash) to judge
Claude-generated outputs. Never uses a model starting with "claude".
"""
from __future__ import annotations

import os

import structlog
from pydantic import BaseModel

log = structlog.get_logger()

_NON_ANTHROPIC_MODELS = {
    "openai": "gpt-4o",
    "google": "gemini-2.5-flash",
}


class JudgeScore(BaseModel):
    model: str
    section: str
    score: float  # 1.0–5.0
    rationale: str


def get_judge_model() -> tuple[str, str] | None:
    """Return (provider, model_name) for available non-Anthropic judge, or None."""
    if os.environ.get("OPENAI_API_KEY"):
        return ("openai", _NON_ANTHROPIC_MODELS["openai"])
    if os.environ.get("GOOGLE_API_KEY"):
        return ("google", _NON_ANTHROPIC_MODELS["google"])
    return None


def judge_text(text: str, section: str, rubric: str, provider: str, model: str) -> JudgeScore:
    """Call non-Anthropic LLM to score text on a rubric (1–5)."""
    assert not model.startswith("claude"), f"Judge model must not be Claude; got {model!r}"

    prompt = f"""Rate the following text on '{section}' using this rubric:
{rubric}

Text to evaluate:
{text}

Respond with JSON: {{"score": <1-5 float>, "rationale": "<1 sentence>"}}"""

    if provider == "openai":
        import json

        import openai

        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=100,
        )
        parsed = json.loads(resp.choices[0].message.content)
    elif provider == "google":
        import json

        import google.generativeai as genai

        genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
        gmodel = genai.GenerativeModel(model)
        resp = gmodel.generate_content(prompt)
        parsed = json.loads(resp.text)
    else:
        raise ValueError(f"Unknown provider: {provider}")

    return JudgeScore(
        model=model,
        section=section,
        score=float(parsed["score"]),
        rationale=parsed.get("rationale", ""),
    )


def score_with_judge(text: str, sections: list[str], rubrics: dict[str, str]) -> list[JudgeScore] | None:
    """Score text across multiple sections. Returns None if no judge available."""
    provider_model = get_judge_model()
    if provider_model is None:
        log.warning("judge.no_provider", msg="No non-Anthropic API key found; skipping judge eval")
        return None
    provider, model = provider_model
    scores = []
    for section in sections:
        rubric = rubrics.get(section, "Rate 1-5 for quality.")
        score = judge_text(text, section, rubric, provider, model)
        scores.append(score)
    return scores
