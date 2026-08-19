"""HRBP AI Workbench — Policy QA Orchestrator.

Orchestrates the full Policy QA flow:
  Query Rewrite → RAG Retrieval → LLM Generation → Output Guard → Citation Bind → No-Evidence Fallback → SSE Stream

This is the per-scenario entry point that coordinates the pipeline.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

from app.config.settings import settings
from app.evaluation.auto_eval import AutoEvaluator
from app.guardrails.input_guard import InputGuardrail
from app.guardrails.output_guard import OutputGuardrail
from app.rag.config_loader import ScenarioConfig, load_scenario_config
from app.rag.llm.orchestrator import LLMOrchestrator
from app.rag.pipeline import _schedule_background_task
from app.rag.retrieval.retriever import Retriever
from app.scenarios.policy_qa.postprocessors import no_evidence_fallback
from app.scenarios.policy_qa.preprocessors import rewrite_query
from app.scenarios.policy_qa.schemas import CitationSource, QAResponse, SSEEvent
from app.shared.logger import get_logger

logger = get_logger(__name__)


class PolicyQAOrchestrator:
    def __init__(self, config: ScenarioConfig | None = None):
        self.config = config or load_scenario_config("policy_qa")
        self.llm = LLMOrchestrator()
        self.retriever = Retriever()
        self.input_guard = InputGuardrail()
        self.output_guard = OutputGuardrail()

    async def execute(self, question: str, tenant_id: str, user_id: str, kb_id: str | None = None) -> QAResponse:
        start_time = time.time()
        rewritten_query = await rewrite_query(question, self.config)
        logger.info("policy_qa_query_rewritten", original=question, rewritten=rewritten_query)

        guarded_input = rewritten_query
        input_flags: dict[str, object] = {}
        if self.config.guardrail_rules.input:
            guarded_input, input_flags = await self.input_guard.check(
                rewritten_query, self.config.guardrail_rules.input
            )
            if input_flags.get("blocked"):
                return QAResponse(
                    answer=str(input_flags.get("block_message", "输入被护栏拦截")),
                    citations=[],
                    confidence=0.0,
                    has_evidence=False,
                    guardrail_flags={"input": input_flags},
                    latency_ms=int((time.time() - start_time) * 1000),
                    tokens_used=0,
                )

        context_chunks = []
        target_kb_id = kb_id or self.config.knowledge_base_id
        if target_kb_id:
            context_chunks = await self.retriever.retrieve(
                query=guarded_input,
                kb_id=target_kb_id,
                strategy=self.config.retrieval_strategy,
                top_k=self.config.retrieval_top_k,
                rerank=self.config.rerank_enabled,
                tenant_id=tenant_id,
            )

        raw_output, tokens_used = await self.llm.generate(
            prompt_template=self.config.prompt_template,
            context=context_chunks,
            query=guarded_input,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        guarded_output = raw_output
        output_flags: dict[str, object] = {}
        if self.config.guardrail_rules.output:
            guarded_output, output_flags = await self.output_guard.check(
                raw_output, self.config.guardrail_rules.output, sources=context_chunks
            )

        final_output = await no_evidence_fallback(guarded_output, self.config, context_chunks)
        confidence = max((float(chunk.get("confidence", 0.0) or 0.0) for chunk in context_chunks), default=0.0)
        citations = [
            CitationSource(
                document_name=s.get("source", "unknown"),
                section=s.get("section", "unknown"),
                content_snippet=s.get("content", "")[:200],
                confidence=min(1.0, max(0.0, float(s.get("confidence", 0.0) or 0.0))),
            )
            for s in context_chunks[:3]
        ]
        latency_ms = int((time.time() - start_time) * 1000)

        if self.config.eval_metrics:
            evaluator = AutoEvaluator()
            _schedule_background_task(
                evaluator.evaluate(
                    output=final_output,
                    query=guarded_input,
                    sources=context_chunks,
                    metrics=self.config.eval_metrics,
                    tenant_id=tenant_id,
                    scenario_id="policy_qa",
                ),
                name="policy_qa_eval_task",
            )

        return QAResponse(
            answer=final_output,
            citations=citations,
            confidence=confidence,
            has_evidence=confidence >= settings.guardrail_confidence_threshold,
            guardrail_flags={"input": input_flags, "output": output_flags},
            latency_ms=latency_ms,
            tokens_used=tokens_used,
        )

    async def execute_stream(
        self, question: str, tenant_id: str, user_id: str, kb_id: str | None = None
    ) -> AsyncIterator[str]:
        start_time = time.time()
        rewritten_query = await rewrite_query(question, self.config)

        if self.config.guardrail_rules.input:
            _, input_flags = await self.input_guard.check(rewritten_query, self.config.guardrail_rules.input)
            if input_flags.get("blocked"):
                event = SSEEvent(
                    event="error",
                    data=json.dumps(
                        {"message": input_flags.get("block_message", "输入被护栏拦截"), "code": "INPUT_BLOCKED"}
                    ),
                )
                yield json.dumps({"event": event.event, "data": event.data})
                return

        context_chunks = []
        target_kb_id = kb_id or self.config.knowledge_base_id
        if target_kb_id:
            context_chunks = await self.retriever.retrieve(
                query=rewritten_query,
                kb_id=target_kb_id,
                strategy=self.config.retrieval_strategy,
                top_k=self.config.retrieval_top_k,
                rerank=self.config.rerank_enabled,
                tenant_id=tenant_id,
            )

        sources_data = [
            {
                "document_name": s.get("source", "unknown"),
                "section": s.get("section", "unknown"),
                "content_snippet": s.get("content", "")[:200],
                "confidence": min(1.0, max(0.0, float(s.get("confidence", 0.0) or 0.0))),
            }
            for s in context_chunks[:3]
        ]
        yield json.dumps({"event": "sources", "data": json.dumps(sources_data)})

        full_output = ""
        output_tokens: int | None = None
        output_flags: dict[str, object] = {}
        try:
            async for chunk_text in self.llm.generate_stream(
                prompt_template=self.config.prompt_template,
                context=context_chunks,
                query=rewritten_query,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
            ):
                full_output += chunk_text
                yield json.dumps({"event": "chunk", "data": json.dumps({"text": chunk_text})})
            if self.config.guardrail_rules.output:
                full_output, output_flags = await self.output_guard.check(
                    full_output, self.config.guardrail_rules.output, sources=context_chunks
                )
            output_tokens = len(full_output.split())
        except Exception:
            logger.exception("policy_qa_stream_error")
            yield json.dumps(
                {
                    "event": "error",
                    "data": json.dumps(
                        {"message": "服务异常，请稍后重试", "code": "INTERNAL_ERROR", "request_id": "unknown"}
                    ),
                }
            )
            return

        final_output = await no_evidence_fallback(full_output, self.config, context_chunks)
        confidence = max((float(chunk.get("confidence", 0.0) or 0.0) for chunk in context_chunks), default=0.0)
        latency_ms = int((time.time() - start_time) * 1000)
        message_id = f"msg_{user_id}_{int(start_time * 1000)}"

        if self.config.eval_metrics:
            evaluator = AutoEvaluator()
            _schedule_background_task(
                evaluator.evaluate(
                    output=final_output,
                    query=rewritten_query,
                    sources=context_chunks,
                    metrics=self.config.eval_metrics,
                    tenant_id=tenant_id,
                    scenario_id="policy_qa",
                ),
                name="policy_qa_stream_eval_task",
            )

        yield json.dumps(
            {
                "event": "done",
                "data": json.dumps(
                    {
                        "message_id": message_id,
                        "confidence": confidence,
                        "has_evidence": confidence >= settings.guardrail_confidence_threshold,
                        "latency_ms": latency_ms,
                        "guardrail_flags": {"input": {}, "output": output_flags},
                        "tokens_used": output_tokens,
                    }
                ),
            }
        )
