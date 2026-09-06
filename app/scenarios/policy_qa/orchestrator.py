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
from app.guardrails.input_guard import InputGuardrail, contains_prompt_injection
from app.guardrails.output_guard import OutputGuardrail
from app.rag.config_loader import ScenarioConfig, load_scenario_config
from app.rag.llm.orchestrator import LLMOrchestrator
from app.rag.pipeline import SegmentTimer, _schedule_background_task
from app.rag.retrieval.retriever import Retriever
from app.scenarios.policy_qa.context_manager import ContextManager, build_policy_qa_messages
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
        self.context = ContextManager()

    async def execute(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        kb_id: str | None = None,
        *,
        history: list[dict[str, str]] | None = None,
        history_meta: dict | None = None,
    ) -> QAResponse:
        start_time = time.time()
        timer = SegmentTimer()
        timer.start("rewrite")
        rewritten_query = await rewrite_query(question, self.config)
        timer.stop("rewrite")
        logger.info("policy_qa_query_rewritten", original=question, rewritten=rewritten_query)

        guarded_input = rewritten_query
        input_flags: dict[str, object] = {}
        if self.config.guardrail_rules.input:
            timer.start("input_guard")
            guarded_input, input_flags = await self.input_guard.check(
                rewritten_query, self.config.guardrail_rules.input
            )
            timer.stop("input_guard")
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
            timer.start("retrieval")
            context_chunks = await self.retriever.retrieve(
                query=guarded_input,
                kb_id=target_kb_id,
                strategy=self.config.retrieval_strategy,
                top_k=self.config.retrieval_top_k,
                rerank=self.config.rerank_enabled,
                tenant_id=tenant_id,
            )
            timer.stop("retrieval")

        if any(contains_prompt_injection(str(chunk.get("content", ""))) for chunk in context_chunks):
            logger.warning("policy_qa_indirect_injection_blocked", tenant_id=tenant_id, kb_id=target_kb_id)
            return QAResponse(
                answer="抱歉，当前制度材料包含潜在的指令性内容，已转为安全回复。请联系 HR 复核原文后再继续。",
                citations=[],
                confidence=0.0,
                has_evidence=False,
                guardrail_flags={
                    "input": input_flags,
                    "output": {"indirect_injection_detected": True, "blocked": True},
                },
                latency_ms=int((time.time() - start_time) * 1000),
                tokens_used=0,
            )

        meta = history_meta or ContextManager.build_meta(None)
        timer.start("llm")
        structured_messages = build_policy_qa_messages(
            prompt_template=self.config.prompt_template,
            query=guarded_input,
            evidence=context_chunks,
            history=history,
        )
        raw_output, tokens_used = await self.llm.generate(
            prompt_template=self.config.prompt_template,
            context=context_chunks,
            query=guarded_input,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            messages=structured_messages,
        )
        timer.stop("llm")

        guarded_output = raw_output
        output_flags: dict[str, object] = {}
        if self.config.guardrail_rules.output:
            timer.start("output_guard")
            guarded_output, output_flags = await self.output_guard.check(
                raw_output, self.config.guardrail_rules.output, sources=context_chunks
            )
            timer.stop("output_guard")

        timer.start("postprocess")
        final_output = await no_evidence_fallback(guarded_output, self.config, context_chunks)
        timer.stop("postprocess")
        confidence = max((float(chunk.get("confidence", 0.0) or 0.0) for chunk in context_chunks), default=0.0)
        # Citations must mirror ONLY the evidence actually backing the answer.
        # When the no-evidence fallback replaced the LLM output, the LLM's
        # (possibly fabricated) claims are gone — so are its citations.
        evidence_used = confidence >= settings.guardrail_confidence_threshold
        citations = self._build_citations(context_chunks) if evidence_used else []
        latency_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "policy_qa_segment_latency",
            tenant_id=tenant_id,
            latency_ms=latency_ms,
            segments_ms=timer.as_metadata(),
            context_chunks=len(context_chunks),
            tokens_used=tokens_used,
            history_meta=meta,
        )

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
                    history_messages=history,
                ),
                name="policy_qa_eval_task",
            )

        return QAResponse(
            answer=final_output,
            citations=citations,
            confidence=confidence,
            has_evidence=evidence_used,
            guardrail_flags={"input": input_flags, "output": output_flags, "history": meta},
            latency_ms=latency_ms,
            tokens_used=tokens_used,
        )

    @staticmethod
    def _build_citations(context_chunks: list[dict]) -> list[CitationSource]:
        """Bind structured citations to the retrieved evidence chunks.

        Mirrors the chunks that were actually fed to generation so
        ``QAResponse.citations`` and the retrieval evidence stay consistent
        (Phase 2: production/evaluation path alignment).
        """
        return [
            CitationSource(
                document_name=s.get("source", "unknown"),
                section=s.get("section", "unknown"),
                content_snippet=s.get("content", "")[:200],
                confidence=min(1.0, max(0.0, float(s.get("confidence", 0.0) or 0.0))),
            )
            for s in context_chunks[:3]
        ]

    async def execute_stream(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        kb_id: str | None = None,
        *,
        history: list[dict[str, str]] | None = None,
        history_meta: dict | None = None,
    ) -> AsyncIterator[str]:
        start_time = time.time()
        timer = SegmentTimer()
        timer.start("rewrite")
        rewritten_query = await rewrite_query(question, self.config)
        timer.stop("rewrite")

        if self.config.guardrail_rules.input:
            timer.start("input_guard")
            _, input_flags = await self.input_guard.check(rewritten_query, self.config.guardrail_rules.input)
            timer.stop("input_guard")
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
            timer.start("retrieval")
            context_chunks = await self.retriever.retrieve(
                query=rewritten_query,
                kb_id=target_kb_id,
                strategy=self.config.retrieval_strategy,
                top_k=self.config.retrieval_top_k,
                rerank=self.config.rerank_enabled,
                tenant_id=tenant_id,
            )
            timer.stop("retrieval")

        if any(contains_prompt_injection(str(chunk.get("content", ""))) for chunk in context_chunks):
            # Mirror the non-stream guard: evidence carrying instruction hijack
            # must never reach the model (and its snippet must not reach the
            # user's evidence panel). Emit a safe terminal event.
            logger.warning("policy_qa_stream_indirect_injection_blocked", tenant_id=tenant_id, kb_id=target_kb_id)
            yield json.dumps(
                {
                    "event": "error",
                    "data": json.dumps(
                        {
                            "message": "当前制度材料包含潜在的指令性内容，已转为安全回复。请联系 HR 复核原文后再继续。",
                            "code": "INDIRECT_INJECTION_BLOCKED",
                        }
                    ),
                }
            )
            return

        sources_data = [
            {
                "document_name": s.get("source", "unknown"),
                "section": s.get("section", "unknown"),
                "content_snippet": s.get("content", "")[:200],
                "confidence": min(1.0, max(0.0, float(s.get("confidence", 0.0) or 0.0))),
            }
            for s in context_chunks[:3]
        ]
        # An empty ``sources`` event is not evidence.  Omitting it lets the
        # history writer persist ``NULL`` and lets downstream feedback detect
        # a genuinely unsupported answer instead of the truthy string "[]".
        if sources_data:
            yield json.dumps({"event": "sources", "data": json.dumps(sources_data)})

        full_output = ""
        output_tokens: int | None = None
        output_flags: dict[str, object] = {}
        first_chunk_ms: int | None = None
        meta = history_meta or ContextManager.build_meta(None)
        try:
            timer.start("llm_first_chunk")
            structured_messages = build_policy_qa_messages(
                prompt_template=self.config.prompt_template,
                query=rewritten_query,
                evidence=context_chunks,
                history=history,
            )
            async for chunk_text in self.llm.generate_stream(
                prompt_template=self.config.prompt_template,
                context=context_chunks,
                query=rewritten_query,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                messages=structured_messages,
            ):
                if first_chunk_ms is None:
                    first_chunk_ms = int((time.time() - start_time) * 1000)
                    timer.stop("llm_first_chunk")
                    timer.start("llm_stream")
                full_output += chunk_text
                yield json.dumps({"event": "chunk", "data": json.dumps({"text": chunk_text})})
            timer.stop("llm_stream")
            if self.config.guardrail_rules.output:
                timer.start("output_guard")
                full_output, output_flags = await self.output_guard.check(
                    full_output, self.config.guardrail_rules.output, sources=context_chunks
                )
                timer.stop("output_guard")
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

        timer.start("postprocess")
        final_output = await no_evidence_fallback(full_output, self.config, context_chunks)
        timer.stop("postprocess")
        confidence = max((float(chunk.get("confidence", 0.0) or 0.0) for chunk in context_chunks), default=0.0)
        latency_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "policy_qa_stream_segment_latency",
            tenant_id=tenant_id,
            latency_ms=latency_ms,
            first_chunk_ms=first_chunk_ms,
            segments_ms=timer.as_metadata(),
            context_chunks=len(context_chunks),
            tokens_used=output_tokens,
            history_meta=meta,
        )
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
                    history_messages=history,
                ),
                name="policy_qa_stream_eval_task",
            )

        yield json.dumps(
            {
                "event": "done",
                "data": json.dumps(
                    {
                        "message_id": message_id,
                        "final_answer": final_output,
                        "confidence": confidence,
                        "has_evidence": confidence >= settings.guardrail_confidence_threshold,
                        "latency_ms": latency_ms,
                        "guardrail_flags": {"input": {}, "output": output_flags, "history": meta},
                        "tokens_used": output_tokens,
                    }
                ),
            }
        )
