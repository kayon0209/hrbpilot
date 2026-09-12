"""HRBP AI Workbench — Output guardrail service."""

from __future__ import annotations

import re

from app.config.settings import settings
from app.guardrails.input_guard import InputGuardrail
from app.rag.retrieval.tokenizer import tokenize
from app.shared.logger import get_logger

logger = get_logger(__name__)

SAFE_RESPONSE = "抱歉，系统检测到输出内容不符合安全规范，已替换为安全回复。请重新提问。"
CITATION_WARNING = "\n\n⚠️ 部分回答内容未能在提供的引用来源中找到明确对应，请谨慎参考。"

# Hard-block terms: unambiguous personal attacks / hate speech. Any output
# containing these is replaced wholesale by SAFE_RESPONSE.
_TOXIC_BLOCK_PATTERNS = (
    "fuck",
    "shit",
    "kill yourself",
    "hate speech",
    "去死",
    "傻逼",
    "杀了你",
    "蠢货",
)

# Warn-only terms: words that are ALSO core HR business vocabulary
# (harassment / discrimination complaints are the most common content of
# interview digests and employee-voice analysis). Reporting an anti-harassment
# procedure must not be censored — flag for review instead of blocking.
_TOXIC_WARN_PATTERNS = (
    "discriminate",
    "harassment",
    "歧视",
    "骚扰",
)


class OutputGuardrail:
    async def check(self, output: str, rules: list[str], sources: list[dict] | None = None) -> tuple[str, dict]:
        flags: dict = {
            "toxicity_detected": False,
            "citation_issues": False,
            "factuality_issues": False,
            "pii_detected": False,
            "blocked": False,
            "warnings": [],
        }
        processed = output
        sources = sources or []

        if settings.guardrail_pii_detection_enabled:
            processed, pii_types = self._detect_and_desensitize_pii(processed)
            if pii_types:
                flags["pii_detected"] = True
                flags["warnings"].append({"pii_types": pii_types})

        if "toxicity_detection" in rules:
            matched = self._match_toxicity(processed)
            if matched is not None:
                flags["toxicity_detected"] = True
                flags["blocked"] = True
                flags["warnings"].append("toxic_content_replaced")
                logger.warning("output_guardrail_toxicity_blocked")
                return SAFE_RESPONSE, flags
            warned = self._match_toxicity_warn(processed)
            if warned:
                # HR-business vocabulary (harassment/discrimination reports):
                # pass through, flag for review. Blocking here would censor the
                # exact scenarios (interview digests, employee voice) whose
                # core content these words describe.
                flags["toxicity_review_flagged"] = True
                flags["warnings"].append("toxic_term_review")
                logger.warning("output_guardrail_toxicity_review_flagged")

        if "citation_verification" in rules and sources and self._verify_citations(processed, sources):
            flags["citation_issues"] = True
            flags["warnings"].append("citation_questionable")
            processed += CITATION_WARNING
            logger.info("output_guardrail_citation_warning")

        if (
            "factuality_check" in rules
            and settings.guardrail_factuality_check_enabled
            and self._check_factuality(processed, sources)
        ):
            flags["factuality_issues"] = True
            flags["warnings"].append("factuality_flagged")
            logger.info("output_guardrail_factuality_flag")

        return processed, flags

    def _detect_toxicity(self, text: str) -> bool:
        """Kept for callers that only need the block verdict."""
        return self._match_toxicity(text) is not None

    @staticmethod
    def _match_toxicity(text: str) -> str | None:
        """Return the first hard-block toxic term, or None."""
        text_lower = text.lower()
        return next((pattern for pattern in _TOXIC_BLOCK_PATTERNS if pattern in text_lower), None)

    @staticmethod
    def _match_toxicity_warn(text: str) -> str | None:
        """Return the first HR-business-vocabulary toxic term, or None."""
        text_lower = text.lower()
        return next((pattern for pattern in _TOXIC_WARN_PATTERNS if pattern in text_lower), None)

    def _detect_and_desensitize_pii(self, text: str) -> tuple[str, list[str]]:
        guard = InputGuardrail()
        return guard._detect_and_desensitize_pii(text)

    def _verify_citations(self, output: str, sources: list[dict]) -> bool:
        citations = self._extract_citation_ids(output)
        if citations:
            valid_ids = {str(source.get("chunk_id", "")).strip() for source in sources if source.get("chunk_id")}
            return not all(citation in valid_ids for citation in citations)

        output_tokens = set(tokenize(output.lower()).split())
        return not any(
            len(output_tokens & set(tokenize(str(source.get("content", "")).lower()).split())) >= 2
            for source in sources
        )

    def _extract_citation_ids(self, output: str) -> list[str]:
        patterns = [r"\[(\d+)\]", r"\[来源(\d+)\]", r"\[chunk:(.+?)\]"]
        ids: list[str] = []
        for pattern in patterns:
            for match in re.findall(pattern, output):
                ids.append(str(match).strip())
        return ids

    def _check_factuality(self, output: str, sources: list[dict]) -> bool:
        if not sources:
            return False
        output_tokens = set(tokenize(output.lower()).split())
        if not output_tokens:
            return False
        source_tokens: set[str] = set()
        for source in sources:
            snippet = str(source.get("content", ""))
            if snippet:
                source_tokens.update(tokenize(snippet.lower()).split())
        if not source_tokens:
            return False
        coverage = len(output_tokens & source_tokens) / max(1, len(output_tokens))
        return coverage < 0.5
