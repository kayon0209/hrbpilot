"""HRBP AI Workbench — Interview Digest Orchestrator.

Orchestrates the full Interview Digest flow:
  File Upload → Document Parsing → PII Desensitization → LLM Structured Extraction → Risk Assessment → Result Storage

Async tasks are persisted to the ``async_tasks`` PostgreSQL table and
dispatched to Celery workers.  ``get_task_status`` reads from the table so
results survive process restarts.
"""

import re
import time
import uuid

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
from app.shared.llm_utils import extract_json_from_llm_output

logger = get_logger(__name__)


def _parse_document_content(raw_text: str) -> str:
    """Parse raw document text — clean up formatting artifacts."""
    text = re.sub(r"\n{3,}", "\n\n", raw_text)
    text = re.sub(r"\t+", " ", text)
    return text.strip()


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

        cleaned_content = _parse_document_content(document_content)
        if not cleaned_content or len(cleaned_content) < 50:
            return InterviewDigestResponse(
                employee_demands=[],
                risk_level=RiskLevel.LOW,
                risk_signals=[],
                action_items=[],
                suggested_owner="",
                summary="访谈记录内容过短，无法进行有效分析",
                confidence=0.0,
                has_evidence=False,
            )

        if self.config.guardrail_rules.input:
            cleaned_content, _input_flags = await self.input_guard.check(
                cleaned_content, self.config.guardrail_rules.input
            )

        raw_output, tokens_used = await self.llm.generate(
            prompt_template=self.config.prompt_template,
            context=[{"source": "访谈记录", "section": "全文", "content": cleaned_content}],
            query="请按指定JSON格式输出结构化抽取结果",
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        parsed = extract_json_from_llm_output(raw_output)

        demands = []
        for d in parsed.get("employee_demands", []):
            try:
                demands.append(
                    Demand(
                        demand=d.get("demand", ""),
                        category=d.get("category", "其他"),
                        urgency=Urgency(d.get("urgency", "中")),
                    )
                )
            except (ValueError, KeyError):
                demands.append(Demand(demand=str(d), category="其他", urgency=Urgency.MEDIUM))

        action_items = []
        for a in parsed.get("action_items", []):
            action_items.append(
                ActionItem(
                    action=a.get("action", ""),
                    owner=a.get("owner", ""),
                    deadline=a.get("deadline", ""),
                )
            )

        risk_level = RiskLevel.LOW
        try:
            risk_level = RiskLevel(parsed.get("risk_level", "LOW"))
        except ValueError:
            pass

        confidence = 0.8 if parsed else 0.3
        latency_ms = int((time.time() - start_time) * 1000)

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
        """Start an async digest task — persists to AsyncTask table, dispatches Celery.

        Returns the task_id so the client can poll the database for progress.
        """
        from app.data.database import get_session_factory
        from app.data.models.infra import AsyncTask

        task_id = str(uuid.uuid4())
        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            task = AsyncTask(
                id=task_id,
                tenant_id=tenant_id,
                type="interview_digest",
                status="pending",
                progress=0,
            )
            db.add(task)
            await db.commit()

        try:
            from app.shared.celery_app import celery_app

            celery_app.send_task(
                "scenario.interview_digest",
                args=[task_id, document_content, tenant_id, user_id],
            )
        except Exception as exc:
            logger.error("interview_digest_dispatch_failed", task_id=task_id, error=str(exc))
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                row = await db.get(AsyncTask, task_id)
                if row:
                    row.status = "failed"
                    row.error_message = str(exc)[:2000]
                    await db.commit()
            raise

        return task_id

    async def get_task_status(self, task_id: str, tenant_id: str) -> DigestStatus | None:
        """Get the status of an async digest task from the database."""
        from sqlalchemy import select

        from app.data.database import get_session_factory
        from app.data.models.infra import AsyncTask

        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            row = (
                (
                    await db.execute(
                        select(AsyncTask).where(AsyncTask.id == task_id, AsyncTask.tenant_id == tenant_id)
                    )
                )
                .scalars()
                .first()
            )
            if not row:
                return None
            result = None
            if row.result_json:
                try:
                    result = InterviewDigestResponse.model_validate_json(row.result_json)
                except Exception:
                    result = None
            return DigestStatus(
                task_id=row.id,
                status=row.status,
                progress=row.progress / 100.0 if row.progress else 0.0,
                result=result,
                error=row.error_message,
            )
