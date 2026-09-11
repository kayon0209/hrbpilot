"""HR Case Agent — bounded planner (Phase 5).

The LLM may only emit a structured CasePlan; the planner re-validates every
step (tool whitelist, schema, risk policy) server-side before anything is
persisted or executed. Budgets: max 5 steps, 1 retry per tool, and the run
stops with handoff when limits are hit. The model output can never reach the
database or an HTTP client directly.
"""

import json
from dataclasses import dataclass, field

from app.scenarios.hr_case_agent.tools import TOOL_KINDS, ToolError, validate_tool_call
from app.shared.logger import get_logger

logger = get_logger(__name__)

MAX_PLAN_STEPS = 5
MAX_TOOL_RETRIES = 1
MAX_STEPS_PER_RUN = 8  # hard run-level fuse (plan steps + retries + reads)
HIGH_RISK_CATEGORIES = frozenset({"termination", "harassment", "discrimination", "labor_arbitration"})
# Writing a case for these needs human review BEFORE approval is even offered.
HUMAN_REVIEW_CATEGORIES = HIGH_RISK_CATEGORIES


class PlanValidationError(Exception):
    """LLM-proposed plan violated the bounded-agent policy."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class PlanStep:
    tool: str
    params: dict
    reason: str = ""
    expected: str = ""


@dataclass
class CasePlanDraft:
    steps: list[PlanStep] = field(default_factory=list)
    rationale: str = ""
    risk_notes: str = ""


class Planner:
    """Turns an LLM JSON proposal into a validated CasePlanDraft.

    ``llm_propose`` is any async callable returning a JSON string. Invalid
    proposals raise PlanValidationError — never silently clipped, so the
    caller (agent loop) hands off instead of executing a mutated plan.
    """

    def __init__(self, llm_propose) -> None:
        self._llm_propose = llm_propose

    async def propose(self, case_context: dict) -> CasePlanDraft:
        raw = await self._llm_propose(case_context)
        return self.validate(raw)

    def validate(self, raw: str) -> CasePlanDraft:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            raise PlanValidationError(f"plan is not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise PlanValidationError("plan must be a JSON object")
        steps_raw = data.get("steps")
        if not isinstance(steps_raw, list) or not steps_raw:
            raise PlanValidationError("plan.steps must be a non-empty list")
        if len(steps_raw) > MAX_PLAN_STEPS:
            raise PlanValidationError(f"plan has {len(steps_raw)} steps; max is {MAX_PLAN_STEPS}")

        steps: list[PlanStep] = []
        write_seen = False
        for i, item in enumerate(steps_raw):
            if not isinstance(item, dict) or "tool" not in item:
                raise PlanValidationError(f"step {i} is not an object with a tool name")
            tool = str(item["tool"])
            params = item.get("params", {})
            if not isinstance(params, dict):
                raise PlanValidationError(f"step {i} params must be an object")
            try:
                normalized = validate_tool_call(tool, params)
            except ToolError as e:
                raise PlanValidationError(f"step {i}: {e.code}") from e

            if TOOL_KINDS[tool] == "write":
                if write_seen:
                    # First plan version keeps a single write action per plan:
                    # one approval, one side effect, simplest audit story.
                    raise PlanValidationError(f"step {i}: at most one write tool per plan")
                write_seen = True

            steps.append(
                PlanStep(
                    tool=tool,
                    params=normalized,
                    reason=str(item.get("reason", ""))[:500],
                    expected=str(item.get("expected", ""))[:200],
                )
            )

        writes = [s for s in steps if TOOL_KINDS[s.tool] == "write"]
        if writes and steps[-1].tool != writes[0].tool:
            # Writes go last: reads gather evidence first, side effect closes.
            raise PlanValidationError("write tool must be the final step")

        return CasePlanDraft(
            steps=steps,
            rationale=str(data.get("rationale", ""))[:1000],
            risk_notes=str(data.get("risk_notes", ""))[:500],
        )


def requires_human_review(category: str, risk_level: str) -> bool:
    """High-risk categories need human review before any write approval."""
    return category in HUMAN_REVIEW_CATEGORIES or risk_level == "HIGH"
