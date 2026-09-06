"""Fail-closed policy decision for the HR Case tool catalog.

Object ACL remains a separate, request-time check in ``HRCaseService``.  This
module intentionally does not infer it from a role or from an approval.
"""

from app.access.middleware.rbac import ROLE_CAPABILITIES
from app.access.policies.contracts import PolicyDecision, ToolCatalog


def evaluate_hr_case_tool(
    catalog: ToolCatalog,
    *,
    tool_name: str,
    tool_version: str,
    subject_id: str,
    subject_role: str,
    object_ref: str,
) -> PolicyDecision:
    tool = catalog.resolve(tool_name, tool_version)
    allowed = tool.required_capability in ROLE_CAPABILITIES.get(subject_role, set())
    return PolicyDecision(
        allowed=allowed,
        reason_code="allowed" if allowed else "missing_required_capability",
        policy_version="rbac-v1",
        evaluated_subject=f"user:{subject_id}",
        evaluated_object=object_ref,
        constraints={
            "required_capability": tool.required_capability,
            "tool_catalog_version": catalog.version,
            "object_acl": "must_be_rechecked_at_execution",
        },
    )
