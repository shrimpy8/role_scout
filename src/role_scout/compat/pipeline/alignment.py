"""On-demand JD alignment & gap analysis via Claude API."""

import json
from pathlib import Path
from string import Template

import anthropic
from bs4 import BeautifulSoup

from role_scout.compat.logging import get_logger
from role_scout.compat.models import ScoredJob

logger = get_logger(__name__)

_MAX_TOKENS = 2048
_TIMEOUT = 60.0

# Path relative to this file: compat/pipeline/alignment.py → role_scout/prompts/alignment_system.md
_ALIGNMENT_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "alignment_system.md"


def run_alignment(job: ScoredJob) -> str:
    """Run JD alignment analysis for a single job; return JSON string result.

    Reads candidate resume and API key from role_scout.config.Settings.

    Raises:
        FileNotFoundError: resume_summary.md or alignment_system.md missing.
        ValueError: Job has no usable description (< 100 chars).
        anthropic.APIStatusError: Claude API call failed.
        json.JSONDecodeError: Claude response could not be parsed as JSON.
    """
    if not job.description or len(job.description.strip()) < 100:
        raise ValueError(f"Job {job.hash_id} has no usable description for alignment")

    from role_scout.config import Settings  # noqa: PLC0415
    settings = Settings()

    resume_path = Path(str(settings.RESUME_SUMMARY_PATH))
    if not resume_path.exists():
        raise FileNotFoundError(
            f"Resume summary not found at {resume_path}. "
            "Create config/resume_summary.md (or set RESUME_SUMMARY_PATH) with your stripped resume content."
        )

    if not _ALIGNMENT_PROMPT_PATH.exists():
        raise FileNotFoundError(f"Alignment prompt not found at {_ALIGNMENT_PROMPT_PATH}")

    resume_summary = resume_path.read_text(encoding="utf-8").strip()
    prompt_template = _ALIGNMENT_PROMPT_PATH.read_text(encoding="utf-8")
    description = job.description
    if "<" in description:
        description = BeautifulSoup(description, "html.parser").get_text(separator=" ", strip=True)
    system_prompt = Template(prompt_template).safe_substitute(
        resume_summary=resume_summary,
        title=job.title,
        company=job.company,
        source=job.source,
        description=description,
    )

    client = anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        timeout=_TIMEOUT,
    )
    response = client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": "Run the alignment analysis."}],
    )
    logger.debug(
        "alignment_api_response",
        hash_id=job.hash_id,
        stop_reason=response.stop_reason,
        content_blocks=len(response.content),
    )

    text_blocks = [b for b in response.content if hasattr(b, "text")]
    if not text_blocks:
        logger.error("alignment_no_text_block", hash_id=job.hash_id, stop_reason=response.stop_reason)
        raise json.JSONDecodeError("Claude returned no text content", "", 0)

    raw = text_blocks[0].text.strip()

    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        logger.error("alignment_parse_no_object", hash_id=job.hash_id, stop_reason=response.stop_reason, excerpt=raw[:500])
        raise json.JSONDecodeError(f"No JSON object in Claude response (stop={response.stop_reason}): {raw[:200]}", raw, 0)

    parsed = json.loads(raw[start:end + 1])
    logger.info("alignment_complete", hash_id=job.hash_id, company=job.company, title=job.title)
    return json.dumps(parsed, ensure_ascii=False)
