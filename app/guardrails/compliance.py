"""HRBP AI Workbench — HR compliance guard checks.

Checks:
  - Salary/compensation confidentiality
  - Privacy regulation compliance (personal data exposure)
  - Internal-only content markers

Applied to both input and output per the architecture spec.
"""

from app.shared.logger import get_logger

logger = get_logger(__name__)

# Keywords indicating potential compliance violations
SALARY_CONFIDENTIAL_PATTERNS = [
    "某人工资", "具体薪资", "同事的薪酬", "某某工资多少",
    "salary of", "how much does X earn",
]

PRIVACY_PATTERNS = [
    "身份证号", "家庭住址", "银行卡号", "体检结果",
    "个人隐私", "电话号",
]


class ComplianceChecker:
    """HR-specific compliance rules for input and output."""

    async def check_input(self, text: str) -> dict:
        """Check user input for HR compliance issues.

        Returns: {flagged: bool, reasons: list[str]}
        """
        result = {"flagged": False, "reasons": []}

        # Salary confidentiality
        if self._contains_any(text, SALARY_CONFIDENTIAL_PATTERNS):
            result["flagged"] = True
            result["reasons"].append("salary_confidentiality")
            logger.info("compliance_flag", type="input", reason="salary_confidentiality")

        # Privacy violation
        if self._contains_any(text, PRIVACY_PATTERNS):
            result["flagged"] = True
            result["reasons"].append("privacy_concern")
            logger.info("compliance_flag", type="input", reason="privacy_concern")

        return result

    async def check_output(self, text: str) -> dict:
        """Check LLM output for HR compliance issues.

        Returns: {flagged: bool, reasons: list[str]}
        """
        result = {"flagged": False, "reasons": []}

        # Output should never expose specific salary data
        if self._contains_any(text, SALARY_CONFIDENTIAL_PATTERNS):
            result["flagged"] = True
            result["reasons"].append("salary_exposure")
            logger.warning("compliance_flag", type="output", reason="salary_exposure")

        # Output should never include PII
        if self._contains_any(text, PRIVACY_PATTERNS):
            result["flagged"] = True
            result["reasons"].append("pii_exposure")
            logger.warning("compliance_flag", type="output", reason="pii_exposure")

        return result

    @staticmethod
    def _contains_any(text: str, patterns: list[str]) -> bool:
        """Check if text contains any of the given patterns."""
        text_lower = text.lower()
        return any(pattern.lower() in text_lower for pattern in patterns)


# Singleton instance
compliance_checker = ComplianceChecker()
