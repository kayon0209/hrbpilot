"""HRBP AI Workbench — Voice Insight Orchestrator.

Orchestrates the full Voice Insight flow:
  Batch Import → Async Analysis → Embedding Clustering → Risk Signal Detection → Trend Analysis → Source Tracing

Uses async task pattern: POST returns task_id, client polls for progress.
"""

import asyncio
import json
import re
import time
from typing import AsyncIterator

from app.rag.config_loader import ScenarioConfig, load_scenario_config
from app.rag.llm.orchestrator import LLMOrchestrator, ZhipuEmbeddingClient
from app.rag.retrieval.retriever import Retriever
from app.guardrails.input_guard import InputGuardrail
from app.guardrails.output_guard import OutputGuardrail
from app.scenarios.voice_insight.schemas import (
    InsightReportResponse, Cluster, RiskSignal, Trend,
    Severity, TrendDirection, TaskStatusResponse,
)
from app.config.settings import settings
from app.shared.logger import get_logger

logger = get_logger(__name__)

# In-memory task store (replace with Redis/DB later)
_task_store: dict[str, TaskStatusResponse] = {}


def _extract_json_from_llm_output(output: str) -> dict:
    """Extract JSON from LLM output."""
    json_match = re.search(r"\{[\s\S]*\}", output)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    return {}


class VoiceInsightOrchestrator:
    """Orchestrator for the Voice Insight scenario."""

    def __init__(self, config: ScenarioConfig | None = None):
        self.config = config or load_scenario_config("voice_insight")
        self.llm = LLMOrchestrator()
        self.input_guard = InputGuardrail()
        self.output_guard = OutputGuardrail()

    async def analyze(
        self,
        documents: list[dict],  # [{"id": "...", "content": "..."}]
        tenant_id: str,
        user_id: str,
    ) -> InsightReportResponse:
        """Analyze a batch of employee voice documents."""
        start_time = time.time()

        # 1. Aggregate all document content
        all_content = ""
        for doc in documents:
            all_content += f"\n--- [来源: {doc.get('id', 'unknown')}] ---\n{doc.get('content', '')}\n"

        if not all_content.strip() or len(all_content) < 100:
            return InsightReportResponse(
                summary="数据不足以做完整分析", confidence=0.0, has_evidence=False,
            )

        # 2. PII desensitization
        if self.config.guardrail_rules.input:
            all_content, input_flags = await self.input_guard.check(
                all_content, self.config.guardrail_rules.input
            )

        # 3. LLM clustering + risk analysis
        raw_output, tokens_used = await self.llm.generate(
            prompt_template=self.config.prompt_template,
            context=[{"source": "员工声音数据", "section": "批量", "content": all_content}],
            query="请按指定JSON格式输出聚类分析、风险信号和趋势分析结果",
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        # 4. Parse structured response
        parsed = _extract_json_from_llm_output(raw_output)

        clusters = []
        for c in parsed.get("clusters", []):
            clusters.append(Cluster(
                label=c.get("label", "未命名"),
                demand_count=c.get("demand_count", 0),
                demands=c.get("demands", []),
                severity=Severity(c.get("severity", "LOW")),
            ))

        risk_signals = []
        for r in parsed.get("risk_signals", []):
            risk_signals.append(RiskSignal(
                signal=r.get("signal", ""),
                severity=Severity(r.get("severity", "MEDIUM")),
                source_ids=r.get("source_ids", []),
                trend=TrendDirection(r.get("trend", "稳定")),
            ))

        trends = []
        for t in parsed.get("trends", []):
            trends.append(Trend(
                topic=t.get("topic", ""),
                direction=TrendDirection(t.get("direction", "稳定")),
                confidence=t.get("confidence", 0.5),
                evidence=t.get("evidence", ""),
            ))

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
        )

        return result

    async def start_async_task(
        self, documents: list[dict], tenant_id: str, user_id: str
    ) -> str:
        """Start async analysis — returns task_id."""
        import uuid
        task_id = str(uuid.uuid4())

        _task_store[task_id] = TaskStatusResponse(
            task_id=task_id, status="pending", progress=0.0,
        )

        async def _run():
            _task_store[task_id].status = "processing"
            _task_store[task_id].progress = 0.3
            try:
                # Simulate multi-step progress
                await asyncio.sleep(0.1)
                _task_store[task_id].progress = 0.6

                result = await self.analyze(documents, tenant_id, user_id)
                _task_store[task_id].status = "completed"
                _task_store[task_id].progress = 1.0
                _task_store[task_id].result = result
            except Exception as e:
                _task_store[task_id].status = "failed"
                _task_store[task_id].error = str(e)

        asyncio.create_task(_run())
        return task_id

    def get_task_status(self, task_id: str) -> TaskStatusResponse | None:
        return _task_store.get(task_id)
