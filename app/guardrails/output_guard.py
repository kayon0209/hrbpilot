"""HRBP AI Workbench — Output guardrail service.

Checks:
  - Citation verification: does the output reference the retrieved sources?
  - Factuality check: does the output contradict known facts?
  - Toxicity detection: is the output safe?
  - Format validation: does output conform to expected schema?

Per architecture spec ADR-004:
  - Citation verification FAIL → mark "citation questionable", don't block
  - Factuality check FAIL → flag for review, don't block
  - Toxicity detected → replace with safe response
"""

from app.rag.retrieval.tokenizer import tokenize
from app.shared.logger import get_logger

logger = get_logger(__name__)

SAFE_RESPONSE = (
    "抱歉，系统检测到输出内容不符合安全规范，已替换为安全回复。请重新提问。"
)

CITATION_WARNING = (
    "\n\n⚠️ 部分回答内容未能在提供的引用来源中找到明确对应，请谨慎参考。"
)


class OutputGuardrail:
    """Check LLM output for safety, accuracy, and format."""

    async def check(
        self,
        output: str,
        rules: list[str],
        sources: list[dict] | None = None,
    ) -> tuple[str, dict]:
        """Apply output guardrail checks.

        Returns: (processed_output, flags_dict)
        """
        flags: dict = {
            "toxicity_detected": False,
            "citation_issues": False,
            "factuality_issues": False,
            "blocked": False,
            "warnings": [],
        }

        processed = output
        sources = sources or []

        # 1. Toxicity detection — block and replace (highest priority)
        if "toxicity_detection" in rules:
            if self._detect_toxicity(processed):
                flags["toxicity_detected"] = True
                flags["blocked"] = True
                flags["warnings"].append("toxic_content_replaced")
                logger.warning("output_guardrail_toxicity_blocked")
                return SAFE_RESPONSE, flags

        # 2. Citation verification — warn, don't block
        if "citation_verification" in rules and sources:
            if self._verify_citations(processed, sources):
                flags["citation_issues"] = True
                flags["warnings"].append("citation_questionable")
                processed += CITATION_WARNING
                logger.info("output_guardrail_citation_warning")

        # 3. Factuality check — flag, don't block
        if "factuality_check" in rules:
            if self._check_factuality(processed, sources):
                flags["factuality_issues"] = True
                flags["warnings"].append("factuality_flagged")
                logger.info("output_guardrail_factuality_flag")

        return processed, flags

    def _detect_toxicity(self, text: str) -> bool:
        """Simple heuristic toxicity detection.

        Production should use a dedicated toxicity classifier model.
        """
        # Heuristic: check for common toxic patterns
        toxic_patterns = [
            "fuck", "shit", "kill yourself", "hate speech",
            "discriminate", "harassment",
        ]
        text_lower = text.lower()
        return any(pattern in text_lower for pattern in toxic_patterns)

    def _verify_citations(self, output: str, sources: list[dict]) -> bool:
        """Check if output claims are backed by source content.

        Returns True if citation issues found.
        """
        # Simplified: check that at least one source snippet appears in output
        for source in sources:
            snippet = source.get("content", "")
            if snippet and len(snippet) > 20:
                # Check for partial overlap
                words = set(tokenize(snippet.lower()).split())
                output_words = set(tokenize(output.lower()).split())
                overlap = words & output_words
                if len(overlap) >= 3:
                    return False  # Found matching source
        # No source matched — potential hallucination
        return bool(sources)  # Only flag if we had sources but found no overlap

    def _check_factuality(self, output: str, sources: list[dict]) -> bool:
        """Placeholder factuality check.

        Production should use an LLM-based factuality verifier
        or a dedicated NLI model.
        """
        # Currently a no-op placeholder. Returns False (no issues found)
        # to avoid false positives in MVP.
        _ = output, sources
        return False
