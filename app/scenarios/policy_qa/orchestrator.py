"""HRBP AI Workbench — Policy QA Orchestrator.

Orchestrates the full Policy QA flow:
  Query Rewrite → RAG Retrieval → LLM Generation → Output Guard → Citation Bind → No-Evidence Fallback → SSE Stream

This is the per-scenario entry point that coordinates the pipeline.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator

from app.config.settings import settings
from app.evaluation.auto_eval import AutoEvaluator
from app.guardrails.input_guard import InputGuardrail
from app.guardrails.output_guard import OutputGuardrail
from app.rag.config_loader import ScenarioConfig
from app.rag.llm.orchestrator import LLMOrchestrator
from app.rag.pipeline import CapabilityPipeline
from app.rag.retrieval.retriever import Retriever
from app.scenarios.policy_qa.postprocessors import no_evidence_fallback
from app.scenarios.policy_qa.preprocessors import rewrite_query
from app.scenarios.policy_qa.schemas import CitationSource, QAResponse, SSEEvent
from app.shared.logger import get_logger

logger = get_logger(__name__)


def _bounded_score(value: object) -> float:
    """Convert native retrieval scores to the response contract's [0, 1] range."""
    if not isinstance(value, (int, float, str)):
        return 0.0
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


# Shared pipeline instance (DI-managed later)
_pipeline = CapabilityPipeline(
    input_guard=InputGuardrail(),
    retriever=Retriever(),
    llm_generator=LLMOrchestrator(),
    output_guard=OutputGuardrail(),
    evaluator=AutoEvaluator(),
)


class PolicyQAOrchestrator:
    """Orchestrator for the Policy QA scenario."""

    def __init__(self, config: ScenarioConfig | None = None):
        from app.rag.config_loader import load_scenario_config

        self.config = config or load_scenario_config("policy_qa")
        self.llm = LLMOrchestrator()
        self.retriever = Retriever()
        self.input_guard = InputGuardrail()
        self.output_guard = OutputGuardrail()

    async def execute(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        kb_id: str | None = None,
    ) -> QAResponse:
        """Execute the full Policy QA pipeline — returns structured response."""
        start_time = time.time()

        # 1. Query rewrite (preprocessor)
        rewritten_query = await rewrite_query(question, self.config)
        logger.info("policy_qa_query_rewritten", original=question, rewritten=rewritten_query)

        # 2. Input guardrail
        guarded_input: str = rewritten_query
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

        # 3. RAG retrieval
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

        # 4. LLM generation
        raw_output, tokens_used = await self.llm.generate(
            prompt_template=self.config.prompt_template,
            context=context_chunks,
            query=guarded_input,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        # 5. Output guardrail
        guarded_output = raw_output
        output_flags: dict[str, object] = {}
        if self.config.guardrail_rules.output:
            guarded_output, output_flags = await self.output_guard.check(
                raw_output, self.config.guardrail_rules.output, sources=context_chunks
            )

        # 6. No-evidence fallback (postprocessor)
        final_output = await no_evidence_fallback(guarded_output, self.config, context_chunks)

        # 7. Build response
        confidence = 0.0
        if context_chunks:
            confidence = max(_bounded_score(chunk.get("confidence", 0.0)) for chunk in context_chunks)

        citations = [
            CitationSource(
                document_name=s.get("source", "unknown"),
                section=s.get("section", "unknown"),
                content_snippet=s.get("content", "")[:200],
                confidence=_bounded_score(s.get("confidence", 0.0)),
            )
            for s in context_chunks[:3]
        ]

        latency_ms = int((time.time() - start_time) * 1000)

        # 8. Async evaluation
        if self.config.eval_metrics:
            evaluator = AutoEvaluator()
            asyncio.create_task(  # noqa: RUF006
                evaluator.evaluate(
                    output=final_output,
                    query=guarded_input,
                    sources=context_chunks,
                    metrics=self.config.eval_metrics,
                    tenant_id=tenant_id,
                )
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
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        kb_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Execute the pipeline with SSE streaming — yields SSEEvent JSON strings."""
        start_time = time.time()

        # 1. Query rewrite
        rewritten_query = await rewrite_query(question, self.config)

        # 2. Input guardrail (blocking check)
        if self.config.guardrail_rules.input:
            _, input_flags = await self.input_guard.check(rewritten_query, self.config.guardrail_rules.input)
            if input_flags.get("blocked"):
                event = SSEEvent(
                    event="error",
                    data=json.dumps(
                        {
                            "message": input_flags.get("block_message", "输入被护栏拦截"),
                            "code": "INPUT_BLOCKED",
                        }
                    ),
                )
                yield json.dumps({"event": event.event, "data": event.data})
                return

        # 3. RAG retrieval (non-streaming)
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

        # 4. Send sources first
        sources_data = [
            {
                "document_name": s.get("source", "unknown"),
                "section": s.get("section", "unknown"),
                "content_snippet": s.get("content", "")[:200],
                "confidence": _bounded_score(s.get("confidence", 0.0)),
            }
            for s in context_chunks[:3]
        ]
        yield json.dumps({"event": "sources", "data": json.dumps(sources_data)})

        # 5. Stream LLM generation
        full_output = ""
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
        except Exception as e:
            logger.error("policy_qa_stream_error", error=str(e))
            yield json.dumps({"event": "error", "data": json.dumps({"message": str(e)})})
            return

        # 6. Output guardrail (on complete output)
        guarded_output = full_output
        if self.config.guardrail_rules.output:
            guarded_output, _output_flags = await self.output_guard.check(
                full_output, self.config.guardrail_rules.output, sources=context_chunks
            )

        # 7. No-evidence fallback
        final_output = await no_evidence_fallback(guarded_output, self.config, context_chunks)

        # If fallback changed the output, send a correction event
        if final_output != guarded_output:
            yield json.dumps({"event": "correction", "data": json.dumps({"full_text": final_output})})

        # 8. Send done event with metadata
        confidence = 0.0
        if context_chunks:
            confidence = max(_bounded_score(chunk.get("confidence", 0.0)) for chunk in context_chunks)

        latency_ms = int((time.time() - start_time) * 1000)

        message_id = f"msg_{user_id}_{int(start_time * 1000)}"

        yield json.dumps(
            {
                "event": "done",
                "data": json.dumps(
                    {
                        "message_id": message_id,
                        "confidence": confidence,
                        "has_evidence": confidence >= settings.guardrail_confidence_threshold,
                        "latency_ms": latency_ms,
                        "guardrail_flags": {},
                    }
                ),
            }
        )
