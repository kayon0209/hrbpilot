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
            if await self._detect_toxicity_async(processed):
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
        """Toxicity detection using heuristic keyword patterns.

        A lightweight check runs synchronously; callers that want deeper
        LLM-based detection should use :meth:`_detect_toxicity_async`.
        """
        toxic_patterns = [
            "fuck", "shit", "kill yourself", "hate speech",
            "discriminate", "harassment",
        ]
        text_lower = text.lower()
        return any(pattern in text_lower for pattern in toxic_patterns)

    async def _detect_toxicity_async(self, text: str) -> bool:
        """LLM-assisted toxicity detection with keyword pre-filter.

        The LLM step is intentionally conservative: if it cannot clearly mark
        the text as unsafe, we fall back to the lightweight keyword filter.
        """
        if self._detect_toxicity(text):
            return True
        if not text.strip():
            return False
        try:
            from app.rag.llm.orchestrator import get_active_model, get_llm_client

            client = get_llm_client()
            model = get_active_model()
            truncated = text[:1500]
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个内容安全审核助手。请判断给定文本是否包含"
                            "仇恨言论、人身攻击、暴力煽动、歧视、骚扰或其他明显有害内容。"
                            "只回答'是'或'否'。"
                        ),
                    },
                    {"role": "user", "content": truncated},
                ],
                max_tokens=10,
                temperature=0.0,
                stream=False,
            )
            answer = (response.choices[0].message.content or "").strip()
            return answer.startswith("是")
        except Exception as e:
            logger.warning("llm_toxicity_check_failed", error=str(e))
            return self._detect_toxicity(text)

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
        """Best-effort factuality check.

        The implementation stays conservative: it only flags when the output
        directly repeats a claim that is not supported by any retrieved source.
        This keeps the guardrail honest without pretending to do deep fact
        verification that the stack does not yet provide.
        """
        if not sources:
            return False

        output_words = set(tokenize(output.lower()).split())
        if not output_words:
            return False

        for source in sources:
            snippet = source.get("content", "")
            if not snippet:
                continue
            source_words = set(tokenize(snippet.lower()).split())
            if not source_words:
                continue
            overlap = output_words & source_words
            if len(overlap) >= 4:
                return False

        return True
