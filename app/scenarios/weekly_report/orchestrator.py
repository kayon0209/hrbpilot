"""HRBP AI Workbench — Weekly Report Orchestrator.

Orchestrates the weekly report generation flow:
  Multi-source Data Aggregation → LLM Generation → Format Adaptation → Draft Save/Publish
"""

import json
import time
from datetime import datetime, timezone

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


class WeeklyReportOrchestrator:
    """Orchestrator for the Weekly Report scenario."""

    def __init__(self, config: ScenarioConfig | None = None):
        self.config = config or load_scenario_config("weekly_report")
        self.llm = LLMOrchestrator()
        self.retriever = Retriever()

    async def generate(
        self,
        period: str,
        source_data: list[dict],
        tenant_id: str,
        user_id: str,
    ) -> WeeklyReportResponse:
        """Generate a weekly report from multi-source data."""
        start_time = time.time()

        aggregated = ""
        for source in source_data:
            aggregated += f"\n--- [来源: {source.get('type', 'unknown')} | ID: {source.get('id', '')}] ---\n{source.get('content', '')}\n"

        if not aggregated.strip():
            return WeeklyReportResponse(
                period=period,
                summary="未收到任何多源数据，无法生成周报",
                confidence=0.0,
                has_evidence=False,
            )

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
            data_sources=parsed.get("data_sources", []),
        )

        logger.info("weekly_report_generated", period=period, latency_ms=latency_ms)
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
            from app.data.database import get_session_factory
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
            logger.error("weekly_report_persist_failed", error=str(exc), user_id=user_id, sources=len(source_data))
            raise
