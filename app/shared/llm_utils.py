"""Shared LLM output utilities used across scenario orchestrators."""

import json
import re

from app.shared.logger import get_logger

logger = get_logger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
_BARE_JSON_RE = re.compile(r"\{[\s\S]*\}")


def extract_json_from_llm_output(output: str) -> dict:
    """Extract a JSON object from LLM output (may have surrounding text or markdown fences).

    Tries fenced ```json blocks first, then falls back to the first bare ``{...}``.
    Returns an empty dict on parse failure.
    """
    # Try markdown-fenced JSON first (most structured output)
    for m in _JSON_BLOCK_RE.finditer(output):
        try:
            return dict(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue

    # Fall back to first bare JSON object
    match = _BARE_JSON_RE.search(output)
    if match:
        try:
            return dict(json.loads(match.group()))
        except json.JSONDecodeError:
            logger.warning("json_parse_failed", output=output[:200])

    return {}
