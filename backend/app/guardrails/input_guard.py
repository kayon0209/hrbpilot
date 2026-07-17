"""HRBP AI Workbench — Input guardrail service.

Checks: PII detection, Prompt injection detection, Topic scope filtering.
Interception strategies:
  - PII → desensitize + warn (don't block)
  - Prompt injection → reject immediately + safety message
  - Topic scope → flag out-of-scope but allow
"""

import re

from app.shared.errors import PromptInjectionError
from app.shared.logger import get_logger

logger = get_logger(__name__)

# Common PII patterns (Chinese context)
PII_PATTERNS = {
    "phone": r"(?:手机|电话|tel)[:\s]*(\d{11})",
    "id_card": r"身份证号[:\s]*\d{17}[\dXx]",
    "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    "bank_card": r"\d{16,19}",  # Simplified
}

# Prompt injection patterns
INJECTION_PATTERNS = [
    r"ignore\s+(previous|above|all)\s+(instructions|rules|constraints)",
    r"(forget|disregard)\s+(everything|all|what)",
    r"you\s+are\s+(now|actually)\s+",
    r"system\s*prompt",
    r"output\s+your\s+(instructions|rules|system)",
    r"pretend\s+(you\s+are|to\s+be)",
]


class InputGuardrail:
    """Check and process input before it reaches LLM."""

    async def check(
        self, input_text: str, rules: list[str]
    ) -> tuple[str, dict]:
        """Apply guardrail rules to input text.

        Returns: (processed_text, flags_dict)
        flags_dict keys: pii_types, has_pii, injection_detected, blocked, block_message
        """
        flags: dict = {
            "has_pii": False,
            "pii_types": [],
            "injection_detected": False,
            "blocked": False,
            "block_message": None,
        }

        processed = input_text

        # PII detection — desensitize, don't block
        if "pii_detection" in rules:
            processed, pii_types = self._detect_and_desensitize_pii(processed)
            if pii_types:
                flags["has_pii"] = True
                flags["pii_types"] = pii_types
                logger.warning("pii_detected", pii_types=pii_types)

        # Prompt injection — block immediately
        if "prompt_injection" in rules:
            if self._detect_prompt_injection(processed):
                flags["injection_detected"] = True
                flags["blocked"] = True
                flags["block_message"] = "输入包含潜在 Prompt 注入内容，已被安全拦截。请正常提问。"
                logger.warning("prompt_injection_blocked", input=input_text[:100])
                return processed, flags

        return processed, flags

    def _detect_and_desensitize_pii(self, text: str) -> tuple[str, list[str]]:
        """Find PII patterns and replace with masked versions."""
        detected_types: list[str] = []

        for pii_type, pattern in PII_PATTERNS.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                detected_types.append(pii_type)
                text = re.sub(pattern, f"[{pii_type}_已脱敏]", text, flags=re.IGNORECASE)

        return text, detected_types

    def _detect_prompt_injection(self, text: str) -> bool:
        """Check for common prompt injection patterns."""
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False
