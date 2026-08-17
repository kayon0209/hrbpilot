"""HRBP AI Workbench — Interview Digest Orchestrator.

Orchestrates the full Interview Digest flow:
  File Upload → Document Parsing → PII Desensitization → LLM Structured Extraction → Risk Assessment → Result Storage

Uses async task pattern: POST returns task_id, client polls for progress.
"""

import asyncio
import json
import re
import time

from app.guardrails.input_guard import InputGuardrail
from app.guardrails.output_guard import OutputGuardrail
from app.rag.config_loader import ScenarioConfig, load_scenario_config
from app.rag.llm.orchestrator import LLMOrchestrator
from app.scenarios.interview_digest.schemas import (
    ActionItem,
    Demand,
    DigestStatus,
    InterviewDigestResponse,
    RiskLevel,
    Urgency,
)
from app.shared.logger import get_logger

logger = get_logger(__name__)


# In-memory task store (replace with Redis/DB later)
_task_store: dict[str, DigestStatus] = {}


def _parse_document_content(raw_text: str) -> str:
    """Parse raw document text — clean up formatting artifacts."""
    # Remove excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", raw_text)
    # Remove common docx artifacts
    text = re.sub(r"\t+", " ", text)
    # Trim
    text = text.strip()
    return text


def _extract_json_from_llm_output(output: str) -> dict:
    """Extract JSON object from LLM output (may have surrounding text)."""
    # Try to find JSON block
    json_match = re.search(r"\{[\s\S]*\}", output)
    if json_match:
        try:
            return dict(json.loads(json_match.group()))
        except json.JSONDecodeError:
            logger.warning("json_parse_failed", output=output[:200])
    # Fallback: return empty dict
    return {}


class InterviewDigestOrchestrator:
    """Orchestrator for the Interview Digest scenario."""

    def __init__(self, config: ScenarioConfig | None = None):
        self.config = config or load_scenario_config("interview_digest")
        self.llm = LLMOrchestrator()
        self.input_guard = InputGuardrail()
        self.output_guard = OutputGuardrail()

    async def digest(self, document_content: str, tenant_id: str, user_id: str) -> InterviewDigestResponse:
        """Process a single interview document — returns structured extraction."""
        start_time = time.time()

        # 1. Parse document content
        cleaned_content = _parse_document_content(document_content)
        if not cleaned_content or len(cleaned_content) < 50:
            return InterviewDigestResponse(
                employee_demands=[], risk_level=RiskLevel.LOW,
                risk_signals=[], action_items=[], suggested_owner="",
                summary="访谈记录内容过短，无法进行有效分析",
                confidence=0.0, has_evidence=False,
            )

        # 2. PII desensitization (input guardrail)
        if self.config.guardrail_rules.input:
            _guarded_content, _input_flags = await self.input_guard.check(
                cleaned_content, self.config.guardrail_rules.input
            )

        # 3. LLM structured extraction
        raw_output, tokens_used = await self.llm.generate(
            prompt_template=self.config.prompt_template,
            context=[{"source": "访谈记录", "section": "全文", "content": cleaned_content}],
            query="请按指定JSON格式输出结构化抽取结果",
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        # 4. Parse LLM output into structured response
        parsed = _extract_json_from_llm_output(raw_output)

        demands = []
        for d in parsed.get("employee_demands", []):
            try:
                demands.append(Demand(
                    demand=d.get("demand", ""),
                    category=d.get("category", "其他"),
                    urgency=Urgency(d.get("urgency", "中")),
                ))
            except (ValueError, KeyError):
                demands.append(Demand(demand=str(d), category="其他", urgency=Urgency.MEDIUM))

        action_items = []
        for a in parsed.get("action_items", []):
            action_items.append(ActionItem(
                action=a.get("action", ""),
                owner=a.get("owner", ""),
                deadline=a.get("deadline", ""),
            ))

        risk_level = RiskLevel.LOW
        try:
            risk_level = RiskLevel(parsed.get("risk_level", "LOW"))
        except ValueError:
            pass

        confidence = 0.8 if parsed else 0.3
        latency_ms = int((time.time() - start_time) * 1000)

        # 5. Output guardrail (toxicity check)
        guarded_summary = parsed.get("summary", "")
        if self.config.guardrail_rules.output:
            guarded_summary, _ = await self.output_guard.check(
                guarded_summary, self.config.guardrail_rules.output, sources=[]
            )

        result = InterviewDigestResponse(
            employee_demands=demands,
            risk_level=risk_level,
            risk_signals=parsed.get("risk_signals", []),
            action_items=action_items,
            suggested_owner=parsed.get("suggested_owner", ""),
            summary=guarded_summary,
            confidence=confidence,
            has_evidence=True,
        )

        logger.info(
            "interview_digest_completed",
            risk_level=risk_level.value,
            demands_count=len(demands),
            latency_ms=latency_ms,
            tokens_used=tokens_used,
        )

        return result

    async def start_async_task(self, document_content: str, tenant_id: str, user_id: str) -> str:
        """Start an async digest task — returns task_id for polling."""
        import uuid
        task_id = str(uuid.uuid4())

        _task_store[task_id] = DigestStatus(
            task_id=task_id, status="pending", progress=0.0,
        )

        # Run in background
        async def _run():
            _task_store[task_id].status = "processing"
            _task_store[task_id].progress = 0.3
            try:
                result = await self.digest(document_content, tenant_id, user_id)
                _task_store[task_id].status = "completed"
                _task_store[task_id].progress = 1.0
                _task_store[task_id].result = result
            except Exception as e:
                _task_store[task_id].status = "failed"
                _task_store[task_id].error = str(e)
                logger.error("interview_digest_task_failed", task_id=task_id, error=str(e))

        asyncio.create_task(_run())  # noqa: RUF006
        return task_id

    def get_task_status(self, task_id: str) -> DigestStatus | None:
        """Get the status of an async digest task."""
        return _task_store.get(task_id)
