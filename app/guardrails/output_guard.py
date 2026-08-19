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

from app.config.settings import settings
from app.rag.retrieval.tokenizer import tokenize
from app.shared.logger import get_logger

logger = get_logger(__name__)

SAFE_RESPONSE = "抱歉，系统检测到输出内容不符合安全规范，已替换为安全回复。请重新提问。"
CITATION_WARNING = "\n\n⚠️ 部分回答内容未能在提供的引用来源中找到明确对应，请谨慎参考。"


class OutputGuardrail:
    """Check LLM output for safety, accuracy, and format."""

    async def check(
        self,
        output: str,
        rules: list[str],
        sources: list[dict] | None = None,
    ) -> tuple[str, dict]:
        flags: dict = {
            "toxicity_detected": False,
            "citation_issues": False,
            "factuality_issues": False,
            "blocked": False,
            "warnings": [],
        }

        processed = output
        sources = sources or []

        if "toxicity_detection" in rules:
            if await self._detect_toxicity_async(processed):
                flags["toxicity_detected"] = True
                flags["blocked"] = True
                flags["warnings"].append("toxic_content_replaced")
                logger.warning("output_guardrail_toxicity_blocked")
                return SAFE_RESPONSE, flags

        if "citation_verification" in rules and sources:
            if self._verify_citations(processed, sources):
                flags["citation_issues"] = True
                flags["warnings"].append("citation_questionable")
                processed += CITATION_WARNING
                logger.info("output_guardrail_citation_warning")

        if "factuality_check" in rules and settings.guardrail_factuality_check_enabled:
            if self._check_factuality(processed, sources):
                flags["factuality_issues"] = True
                flags["warnings"].append("factuality_flagged")
                logger.info("output_guardrail_factuality_flag")

        return processed, flags

    def _detect_toxicity(self, text: str) -> bool:
        toxic_patterns = [
            "fuck", "shit", "kill yourself", "hate speech", "discriminate", "harassment",
            "去死", "傻逼", "垃圾", "歧视", "骚扰", "杀了你", "蠢货",
        ]
        text_lower = text.lower()
        return any(pattern in text_lower for pattern in toxic_patterns)

    async def _detect_toxicity_async(self, text: str) -> bool:
        if self._detect_toxicity(text):
            return True
        return False

    def _verify_citations(self, output: str, sources: list[dict]) -> bool:
        citations = self._extract_citation_ids(output)
        if citations:
            valid_ids = {str(source.get("chunk_id", "")).strip() for source in sources if source.get("chunk_id")}
            return not all(citation in valid_ids for citation in citations)

        output_tokens = set(tokenize(output.lower()).split())
        if not output_tokens:
            return bool(sources)

        for source in sources:
            snippet = str(source.get("content", ""))
            if not snippet:
                continue
            source_tokens = set(tokenize(snippet.lower()).split())
            if not source_tokens:
                continue
            overlap = output_tokens & source_tokens
            if overlap:
                return False

        return bool(sources)

    def _extract_citation_ids(self, output: str) -> list[str]:
        import re

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

        weighted_overlap = 0.0
        total_weight = 0.0
        for source in sources:
            snippet = str(source.get("content", ""))
            if not snippet:
                continue
            source_tokens = set(tokenize(snippet.lower()).split())
            if not source_tokens:
                continue
            weight = min(1.0, len(source_tokens) / 50.0)
            overlap = len(output_tokens & source_tokens)
            weighted_overlap += overlap * weight
            total_weight += weight

        return total_weight > 0 and (weighted_overlap / total_weight) < 1.5
