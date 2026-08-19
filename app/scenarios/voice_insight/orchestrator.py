"""HRBP AI Workbench — Voice Insight Orchestrator.

Orchestrates the full Voice Insight flow:
  Batch Import → Async Analysis → Embedding Clustering → Risk Signal Detection → Trend Analysis → Source Tracing

Async tasks are persisted to the ``async_tasks`` PostgreSQL table and
persisted results are written to the scenario tables.
"""

from __future__ import annotations

import json
import time
import uuid

from sqlalchemy import select

from app.data.database import get_session_factory
from app.data.models.infra import AsyncTask
from app.guardrails.input_guard import InputGuardrail
from app.guardrails.output_guard import OutputGuardrail
from app.rag.config_loader import ScenarioConfig, load_scenario_config
from app.rag.llm.orchestrator import LLMOrchestrator
from app.scenarios.voice_insight.schemas import (
    Cluster,
    InsightReportResponse,
    RiskSignal,
    Severity,
    TaskStatusResponse,
    Trend,
    TrendDirection,
)
from app.shared.logger import get_logger
from app.shared.llm_utils import extract_json_from_llm_output

logger = get_logger(__name__)


def _summarize_async_task_result(result_json: str | None) -> str:
    """Turn a stored async task payload into compact prompt text."""
    if not result_json:
        return ""
    try:
        payload = json.loads(result_json)
    except Exception:
        return result_json[:2000]

    if not isinstance(payload, dict):
        return result_json[:2000]

    lines: list[str] = []
    summary = str(payload.get("summary", "")).strip()
    if summary:
        lines.append(f"摘要: {summary}")

    clusters = payload.get("clusters") or []
    if isinstance(clusters, list) and clusters:
        cluster_text = "; ".join(
            str(item.get("label", item)).strip()
            for item in clusters[:5]
            if str(item.get("label", item)).strip()
        )
        if cluster_text:
            lines.append(f"聚类: {cluster_text}")

    signals = payload.get("risk_signals") or []
    if isinstance(signals, list) and signals:
        signal_text = "; ".join(
            str(item.get("signal", item)).strip()
            for item in signals[:5]
            if str(item.get("signal", item)).strip()
        )
        if signal_text:
            lines.append(f"风险信号: {signal_text}")

    trends = payload.get("trends") or []
    if isinstance(trends, list) and trends:
        trend_text = "; ".join(
            str(item.get("topic", item)).strip()
            for item in trends[:5]
            if str(item.get("topic", item)).strip()
        )
        if trend_text:
            lines.append(f"趋势: {trend_text}")

    return "\n".join(lines) if lines else result_json[:2000]


class VoiceInsightOrchestrator:
    """Orchestrator for the Voice Insight scenario."""

    def __init__(self, config: ScenarioConfig | None = None):
        self.config = config or load_scenario_config("voice_insight")
        self.llm = LLMOrchestrator()
        self.input_guard = InputGuardrail()
        self.output_guard = OutputGuardrail()

    async def _resolve_documents(
        self,
        tenant_id: str,
        documents: list[dict],
    ) -> tuple[list[dict], list[str]]:
        """Resolve document IDs to prompt text in one batched query."""
        doc_ids = [str(doc.get("id", "")).strip() for doc in documents if doc.get("id")]
        if not doc_ids:
            return [], []

        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            rows = (
                (
                    await db.execute(
                        select(AsyncTask).where(
                            AsyncTask.tenant_id == tenant_id,
                            AsyncTask.id.in_(doc_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )

        rows_by_id = {row.id: row for row in rows}
        resolved: list[dict] = []
        skipped: list[str] = []

        for doc in documents:
            doc_id = str(doc.get("id", "")).strip()
            if not doc_id:
                continue
            row = rows_by_id.get(doc_id)
            if not row or not row.result_json:
                skipped.append(doc_id)
                continue
            resolved.append(
                {
                    "id": doc_id,
                    "content": _summarize_async_task_result(row.result_json),
                }
            )

        return resolved, skipped

    async def analyze(
        self,
        documents: list[dict],  # [{"id": "...", "content": "..."}]
        tenant_id: str,
        user_id: str,
    ) -> InsightReportResponse:
        """Analyze a batch of employee voice documents."""
        start_time = time.time()

        resolved_documents, skipped_documents = await self._resolve_documents(tenant_id, documents)
        if not resolved_documents:
            return InsightReportResponse(
                summary="数据不足以做完整分析",
                confidence=0.0,
                has_evidence=False,
            )

        all_content = ""
        for doc in resolved_documents:
            all_content += f"\n--- [来源: {doc.get('id', 'unknown')}] ---\n{doc.get('content', '')}\n"

        if self.config.guardrail_rules.input:
            all_content, _input_flags = await self.input_guard.check(
                all_content, self.config.guardrail_rules.input
            )

        raw_output, _tokens_used = await self.llm.generate(
            prompt_template=self.config.prompt_template,
            context=[{"source": "员工声音数据", "section": "批量", "content": all_content}],
            query="请按指定JSON格式输出聚类分析、风险信号和趋势分析结果",
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        parsed = extract_json_from_llm_output(raw_output)

        clusters = []
        for c in parsed.get("clusters", []):
            clusters.append(
                Cluster(
                    label=c.get("label", "未命名"),
                    demand_count=c.get("demand_count", 0),
                    demands=c.get("demands", []),
                    severity=Severity(c.get("severity", "LOW")),
                )
            )

        risk_signals = []
        for r in parsed.get("risk_signals", []):
            risk_signals.append(
                RiskSignal(
                    signal=r.get("signal", ""),
                    severity=Severity(r.get("severity", "MEDIUM")),
                    source_ids=r.get("source_ids", []),
                    trend=TrendDirection(r.get("trend", "稳定")),
                )
            )

        trends = []
        for t in parsed.get("trends", []):
            trends.append(
                Trend(
                    topic=t.get("topic", ""),
                    direction=TrendDirection(t.get("direction", "稳定")),
                    confidence=t.get("confidence", 0.5),
                    evidence=t.get("evidence", ""),
                )
            )

        latency_ms = int((time.time() - start_time) * 1000)

        result = InsightReportResponse(
            clusters=clusters,
            risk_signals=risk_signals,
            trends=trends,
            summary=parsed.get("summary", ""),
            confidence=0.8 if parsed else 0.3,
            has_evidence=True,
        )

        logger.info(
            "voice_insight_completed",
            clusters=len(clusters),
            risk_signals=len(risk_signals),
            latency_ms=latency_ms,
            skipped_documents=skipped_documents,
        )

        return result

    async def start_async_task(
        self, documents: list[dict], tenant_id: str, user_id: str
    ) -> str:
        """Start async analysis — persists to AsyncTask table, dispatches Celery."""
        from app.data.models.infra import AsyncTask

        task_id = str(uuid.uuid4())
        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            task = AsyncTask(
                id=task_id,
                tenant_id=tenant_id,
                type="voice_insight",
                status="pending",
                progress=0,
            )
            db.add(task)
            await db.commit()

        try:
            from app.shared.celery_app import celery_app

            celery_app.send_task(
                "scenario.voice_insight",
                args=[task_id, json.dumps(documents), tenant_id, user_id],
            )
        except Exception as exc:
            logger.error("voice_insight_dispatch_failed", task_id=task_id, error=str(exc))
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                row = await db.get(AsyncTask, task_id)
                if row:
                    row.status = "failed"
                    row.error_message = str(exc)[:2000]
                    await db.commit()
            raise

        return task_id

    async def get_task_status(self, task_id: str, tenant_id: str) -> TaskStatusResponse | None:
        """Get the status of an async voice insight task from the database."""
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
                    result = InsightReportResponse.model_validate_json(row.result_json)
                except Exception:
                    result = None
            return TaskStatusResponse(
                task_id=row.id,
                status=row.status,
                progress=row.progress / 100.0 if row.progress else 0.0,
                result=result,
                error=row.error_message,
            )
