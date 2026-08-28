"""HRBP AI Workbench — Input guardrail service.

Checks: PII detection, Prompt injection detection, Topic scope filtering.
Interception strategies:
  - PII → desensitize + warn (don't block)
  - Prompt injection → reject immediately + safety message
  - Topic scope → flag out-of-scope but allow
"""

import re

from app.shared.logger import get_logger

logger = get_logger(__name__)

# Common PII patterns (Chinese context)
PII_PATTERNS = {
    "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    "phone": r"(?<!\d)1[3-9]\d{9}(?!\d)",
    "id_card": r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[0-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)",
}

BANK_CARD_PATTERN = r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"

# Prompt injection patterns (English + Chinese)
# Keep the set narrow to avoid blocking legitimate HR prompts that mention
# "提示词" or "角色扮演" in a harmless instructional context.
INJECTION_PATTERNS = [
    # English instruction hijacking
    r"ignore\s+(?:all\s+)?(?:previous|above|earlier)\s+(?:instructions?|rules?|constraints?|prompts?)",
    r"(forget|disregard|override|bypass)\s+(?:all\s+)?(?:previous|above|earlier|the)\s+(?:instructions?|rules?|constraints?|prompts?)",
    r"(forget|disregard)\s+everything",
    r"(forget|disregard|override|bypass)\s+all\s+(?:constraints?|rules?|instructions?|limits?|restrictions?)",
    r"unrestricted\s+(?:ai|assistant|mode)",
    r"you\s+are\s+now\s+(?:going\s+to\s+)?(?:act\s+as|behave\s+as|become)\s+",
    r"system\s+message\s*[:：]",
    r"system\s+prompt\s*[:：]",
    r"developer\s+message\s*[:：]",
    r"reveal\s+(?:the\s+)?(?:system|developer)\s+(?:prompt|message|instructions?)",
    r"print\s+(?:the\s+)?(?:system|developer)\s+(?:prompt|message|instructions?)",
    # Chinese instruction hijacking
    r"忽略\s*(?:以上|上面|之前|前面|所有|全部)?\s*(?:的)?\s*(?:指令|规则|要求|约束|提示|提示词)",
    r"无视\s*(?:以上|上面|之前|前面|所有|全部)?\s*(?:的)?\s*(?:指令|规则|要求|约束|提示|提示词)",
    r"覆盖\s*(?:以上|上面|之前|前面|所有|全部)?\s*(?:的)?\s*(?:指令|规则|要求|约束)",
    r"你现在是(?:一个)?\s*(?:不受限制|无限制)的?\s*(?:ai|助手|系统)",
    r"作为(?:一个)?\s*(?:不受限制|无限制)的?\s*(?:ai|助手|系统)",
    r"输出\s*(?:你的)?\s*(?:系统|开发者)?\s*(?:提示词|提示|规则|指令)",
    # Role-play impersonation aimed at data exfiltration ("假装你是CEO…")
    r"假装\s*你是",
]


class InputGuardrail:
    """Check and process input before it reaches LLM."""

    async def check(self, input_text: str, rules: list[str]) -> tuple[str, dict]:
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

        bank_matches = re.findall(BANK_CARD_PATTERN, text)
        for match in bank_matches:
            compact = re.sub(r"[ -]", "", match)
            if self._luhn_valid(compact):
                detected_types.append("bank_card")
                text = text.replace(match, "[bank_card_已脱敏]")

        return text, detected_types

    def _luhn_valid(self, digits: str) -> bool:
        if not digits.isdigit() or len(digits) < 13 or len(digits) > 19:
            return False
        checksum = 0
        reverse_digits = digits[::-1]
        for idx, ch in enumerate(reverse_digits):
            n = int(ch)
            if idx % 2 == 1:
                n *= 2
                if n > 9:
                    n -= 9
            checksum += n
        return checksum % 10 == 0

    def _detect_prompt_injection(self, text: str) -> bool:
        """Check for common prompt injection patterns."""
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False
