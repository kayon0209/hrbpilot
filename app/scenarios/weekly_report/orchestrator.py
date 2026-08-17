"""HRBP AI Workbench — Weekly Report Orchestrator.

Orchestrates the weekly report generation flow:
  Multi-source Data Aggregation → LLM Generation → Format Adaptation → Draft Save/Publish
"""

import json
import re
import time

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

logger = get_logger(__name__)

# In-memory report store (replace with DB later)
_report_store: dict[str, WeeklyReportResponse] = {}


def _extract_json_from_llm_output(output: str) -> dict:
    json_match = re.search(r"\{[\s\S]*\}", output)
    if json_match:
        try:
            return dict(json.loads(json_match.group()))
        except json.JSONDecodeError:
            pass
    return {}


class WeeklyReportOrchestrator:
    """Orchestrator for the Weekly Report scenario."""

    def __init__(self, config: ScenarioConfig | None = None):
        self.config = config or load_scenario_config("weekly_report")
        self.llm = LLMOrchestrator()
        self.retriever = Retriever()

    async def generate(
        self,
        period: str,
        source_data: list[dict],  # [{"type": "interview_digest", "content": "..."}]
        tenant_id: str,
        user_id: str,
    ) -> WeeklyReportResponse:
        """Generate a weekly report from multi-source data."""
        start_time = time.time()

        # 1. Aggregate source data
        aggregated = ""
        for source in source_data:
            aggregated += f"\n--- [来源: {source.get('type', 'unknown')} | ID: {source.get('id', '')}] ---\n{source.get('content', '')}\n"

        if not aggregated.strip():
            return WeeklyReportResponse(
                period=period, summary="无数据来源，无法生成周报",
                confidence=0.0, has_evidence=False,
            )

        # 2. RAG retrieval (optional: supplement with KB context)
        kb_context = []
        if self.config.knowledge_base_id:
            kb_context = await self.retriever.retrieve(
                query=f"HR周报 {period} 进展 风险 计划",
                kb_id=self.config.knowledge_base_id,
                strategy=self.config.retrieval_strategy,
                top_k=3,
                tenant_id=tenant_id,
            )

        # 3. LLM generation
        raw_output, _tokens_used = await self.llm.generate(
            prompt_template=self.config.prompt_template,
            context=[{"source": "多源数据", "section": "聚合", "content": aggregated}, *kb_context],
            query=f"请为 {period} 周期生成结构化周报",
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        # 4. Parse response
        parsed = _extract_json_from_llm_output(raw_output)

        progress = []
        for p in parsed.get("progress", []):
            progress.append(ProgressItem(
                item=p.get("item", ""),
                source=p.get("source", ""),
                status=TaskStatus(p.get("status", "进行中")),
            ))

        risks = []
        for r in parsed.get("risks", []):
            risks.append(RiskItem(
                risk=r.get("risk", ""),
                severity=Severity(r.get("severity", "MEDIUM")),
                owner=r.get("owner", ""),
                action=r.get("action", ""),
            ))

        plan = []
        for pl in parsed.get("plan", []):
            plan.append(PlanItem(
                task=pl.get("task", ""),
                priority=Priority(pl.get("priority", "中")),
                deadline=pl.get("deadline", ""),
            ))

        latency_ms = int((time.time() - start_time) * 1000)

        result = WeeklyReportResponse(
            period=parsed.get("period", period),
            summary=parsed.get("summary", ""),
            progress=progress,
            risks=risks,
            plan=plan,
            data_sources=parsed.get("data_sources", []),
            confidence=0.75 if parsed else 0.3,
            has_evidence=True,
        )

        logger.info("weekly_report_generated", period=period, latency_ms=latency_ms)

        return result

    def save_report(self, report_id: str, report: WeeklyReportResponse):
        """Save a report to in-memory store."""
        _report_store[report_id] = report

    def get_report(self, report_id: str) -> WeeklyReportResponse | None:
        return _report_store.get(report_id)
