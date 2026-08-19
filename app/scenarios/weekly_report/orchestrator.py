"""HRBP AI Workbench — Weekly Report Orchestrator.

Orchestrates the weekly report generation flow:
  Multi-source Data Aggregation → LLM Generation → Format Adaptation → Draft Save/Publish
"""

from __future__ import annotations

import json
import time

from sqlalchemy import select

from app.data.database import get_session_factory
from app.data.models.infra import AsyncTask
from app.rag.config_loader import ScenarioConfig, load_scenario_config
from app.rag.llm.orchestrator import LLMOrchestrator
from app.rag.retrieval.retriever import Retriever
from app.scenarios.weekly_report.schemas import (
    PlanItem,
    Priority,
    ProgressItem,
    RiskItem,
    Severity,
    TaskStatus,
    WeeklyReportResponse,
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

    demands = payload.get("employee_demands") or []
    if isinstance(demands, list) and demands:
        demand_text = "; ".join(
            str(item.get("demand", item)).strip()
            for item in demands[:5]
            if str(item.get("demand", item)).strip()
        )
        if demand_text:
            lines.append(f"诉求: {demand_text}")

    risk_signals = payload.get("risk_signals") or []
    if isinstance(risk_signals, list) and risk_signals:
        risk_text = "; ".join(str(item).strip() for item in risk_signals[:5] if str(item).strip())
        if risk_text:
            lines.append(f"风险信号: {risk_text}")

    action_items = payload.get("action_items") or []
    if isinstance(action_items, list) and action_items:
        action_text = "; ".join(
            str(item.get("action", item)).strip()
            for item in action_items[:5]
            if str(item.get("action", item)).strip()
        )
        if action_text:
            lines.append(f"行动项: {action_text}")

    owner = str(payload.get("suggested_owner", "")).strip()
    if owner:
        lines.append(f"建议负责人: {owner}")

    return "\n".join(lines) if lines else result_json[:2000]


class WeeklyReportOrchestrator:
    """Orchestrator for the Weekly Report scenario."""

    def __init__(self, config: ScenarioConfig | None = None):
        self.config = config or load_scenario_config("weekly_report")
        self.llm = LLMOrchestrator()
        self.retriever = Retriever()

    async def _resolve_sources(
        self,
        tenant_id: str,
        source_data: list[dict],
    ) -> tuple[list[dict], list[str]]:
        """Resolve source IDs to prompt text in one batched query."""
        source_ids = [str(source.get("id", "")).strip() for source in source_data if source.get("id")]
        if not source_ids:
            return [], []

        factory = get_session_factory()
        async with factory() as db:
            db.info["tenant_id"] = tenant_id
            rows = (
                (
                    await db.execute(
                        select(AsyncTask).where(
                            AsyncTask.tenant_id == tenant_id,
                            AsyncTask.id.in_(source_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )

        rows_by_id = {row.id: row for row in rows}
        resolved: list[dict] = []
        skipped: list[str] = []

        for source in source_data:
            source_id = str(source.get("id", "")).strip()
            if not source_id:
                continue
            row = rows_by_id.get(source_id)
            if not row or not row.result_json:
                skipped.append(source_id)
                continue
            resolved.append(
                {
                    "type": source.get("type", "unknown"),
                    "id": source_id,
                    "content": _summarize_async_task_result(row.result_json),
                }
            )

        return resolved, skipped

    async def generate(
        self,
        period: str,
        source_data: list[dict],
        tenant_id: str,
        user_id: str,
    ) -> WeeklyReportResponse:
        """Generate a weekly report from multi-source data."""
        start_time = time.time()

        resolved_sources, skipped_sources = await self._resolve_sources(tenant_id, source_data)
        if not resolved_sources:
            return WeeklyReportResponse(
                period=period,
                summary="未收到可用的多源数据，无法生成周报",
            )

        aggregated = ""
        for source in resolved_sources:
            aggregated += f"\n--- [来源: {source.get('type', 'unknown')} | ID: {source.get('id', '')}] ---\n{source.get('content', '')}\n"

        kb_context = []
        if self.config.knowledge_base_id:
            kb_context = await self.retriever.retrieve(
                query=f"HR周报 {period} 进展 风险 计划",
                kb_id=self.config.knowledge_base_id,
                strategy=self.config.retrieval_strategy,
                top_k=3,
                tenant_id=tenant_id,
            )

        raw_output, _tokens_used = await self.llm.generate(
            prompt_template=self.config.prompt_template,
            context=[{"source": "多源数据", "section": "聚合", "content": aggregated}, *kb_context],
            query=f"请为 {period} 周期生成结构化周报",
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        parsed = extract_json_from_llm_output(raw_output)

        progress = []
        for p in parsed.get("progress", []):
            progress.append(
                ProgressItem(
                    item=p.get("item", ""),
                    source=p.get("source", ""),
                    status=TaskStatus(p.get("status", "进行中")),
                )
            )

        risks = []
        for r in parsed.get("risks", []):
            risks.append(
                RiskItem(
                    risk=r.get("risk", ""),
                    severity=Severity(r.get("severity", "MEDIUM")),
                    owner=r.get("owner", ""),
                    action=r.get("action", ""),
                )
            )

        plan = []
        for pl in parsed.get("plan", []):
            plan.append(
                PlanItem(
                    task=pl.get("task", ""),
                    priority=Priority(pl.get("priority", "中")),
                    deadline=pl.get("deadline", ""),
                )
            )

        latency_ms = int((time.time() - start_time) * 1000)

        result = WeeklyReportResponse(
            period=parsed.get("period", period),
            summary=parsed.get("summary", ""),
            progress=progress,
            risks=risks,
            plan=plan,
            data_sources=[source["id"] for source in resolved_sources],
        )

        logger.info(
            "weekly_report_generated",
            period=period,
            latency_ms=latency_ms,
            skipped_sources=skipped_sources,
        )
        return result

    async def _store_report(
        self,
        tenant_id: str,
        user_id: str,
        report: WeeklyReportResponse,
        source_data: list[dict],
    ) -> str:
        """Persist the weekly report to PostgreSQL and return the database ID."""
        try:
            from app.data.models.scenarios import WeeklyReport

            factory = get_session_factory()
            async with factory() as db:
                db.info["tenant_id"] = tenant_id
                record = WeeklyReport(
                    tenant_id=tenant_id,
                    period=report.period,
                    summary=report.summary,
                    progress_json=json.dumps([item.model_dump() for item in report.progress], ensure_ascii=False),
                    risks_json=json.dumps([item.model_dump() for item in report.risks], ensure_ascii=False),
                    plan_json=json.dumps([item.model_dump() for item in report.plan], ensure_ascii=False),
                    data_sources_json=json.dumps(source_data, ensure_ascii=False),
                )
                db.add(record)
                await db.commit()
                await db.refresh(record)
                return record.id
        except Exception as exc:
            logger.warning("weekly_report_persist_failed", error=str(exc), user_id=user_id, sources=len(source_data))
            return ""
