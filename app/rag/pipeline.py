"""HRBP AI Workbench — CapabilityPipeline.

The shared pipeline that all 5 scenarios run through.
Flow: InputGuard → RAG Retrieval → LLM Generation → OutputGuard → Citation Binding → Eval (async)

ScenarioConfig injects per-scenario differences.
Pipeline code is zero-modification — all variance comes from config.
"""

import asyncio
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from app.rag.config_loader import ScenarioConfig
from app.shared.errors import AppError, ExternalServiceError
from app.shared.logger import get_logger

logger = get_logger(__name__)

_background_tasks: set[asyncio.Task[Any]] = set()


def _schedule_background_task(coro: Coroutine[Any, Any, Any], *, name: str) -> None:
    """Keep non-blocking work alive and surface unexpected failures."""
    task: asyncio.Task[Any] = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)

    def _on_complete(done_task: asyncio.Task[Any]) -> None:
        _background_tasks.discard(done_task)
        if done_task.cancelled():
            return
        try:
            error = done_task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.error("pipeline_background_task_failed", task_name=name, error=str(error))

    task.add_done_callback(_on_complete)


@dataclass
class PipelineResult:
    """Output from a pipeline execution."""
    output: str
    sources: list[dict]  # Retrieved document chunks with metadata
    confidence: float
    guardrail_flags: dict  # Which guardrails triggered
    latency_ms: int
    tokens_used: int | None = None


class CapabilityPipeline:
    """Main pipeline — orchestrates the 6-step flow for every scenario."""

    def __init__(
        self,
        input_guard=None,
        retriever=None,
        llm_generator=None,
        output_guard=None,
        citation_binder=None,
        evaluator=None,
    ) -> None:
        self.input_guard = input_guard
        self.retriever = retriever
        self.llm_generator = llm_generator
        self.output_guard = output_guard
        self.citation_binder = citation_binder
        self.evaluator = evaluator

    async def execute(
        self,
        input: str,
        config: ScenarioConfig,
        tenant_id: str,
        user_id: str,
        preprocessor=None,
        postprocessor=None,
    ) -> PipelineResult:
        """Execute the full pipeline for one request."""
        start_time = time.time()
        guardrail_flags = {}

        # 0. Preprocessor (optional, per-scenario)
        processed_input = input
        if preprocessor:
            processed_input = await preprocessor(input, config)

        # 1. Input guardrail
        if self.input_guard and config.guardrail_rules.input:
            guarded_input, input_flags = await self.input_guard.check(
                processed_input, config.guardrail_rules.input
            )
            guardrail_flags["input"] = input_flags
            if input_flags.get("blocked"):
                return PipelineResult(
                    output=input_flags.get("block_message", "输入被护栏拦截"),
                    sources=[], confidence=0.0,
                    guardrail_flags=guardrail_flags,
                    latency_ms=int((time.time() - start_time) * 1000),
                )
        else:
            guarded_input = processed_input

        # 2. RAG retrieval (optional — some scenarios may skip)
        context_chunks = []
        if self.retriever and config.knowledge_base_id:
            try:
                context_chunks = await self.retriever.retrieve(
                    query=guarded_input,
                    kb_id=config.knowledge_base_id,
                    strategy=config.retrieval_strategy,
                    top_k=config.retrieval_top_k,
                    rerank=config.rerank_enabled,
                    tenant_id=tenant_id,
                )
            except AppError:
                raise
            except Exception as e:
                logger.error("retrieval_failed", error=str(e))
                raise ExternalServiceError("retrieval", str(e)) from e

        # ``score`` is strategy-specific ranking data (RRF, cosine, or FTS),
        # while ``confidence`` is calibrated to the 0..1 guardrail scale.
        confidence = 0.0
        if context_chunks:
            confidence = max(chunk.get("confidence", 0.0) for chunk in context_chunks)

        # 3. LLM generation
        if self.llm_generator:
            raw_output, tokens_used = await self.llm_generator.generate(
                prompt_template=config.prompt_template,
                context=context_chunks,
                query=guarded_input,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
            )
        else:
            raw_output = "(LLM 未配置)"
            tokens_used = None

        # 4. Output guardrail
        if self.output_guard and config.guardrail_rules.output:
            guarded_output, output_flags = await self.output_guard.check(
                raw_output, config.guardrail_rules.output, sources=context_chunks
            )
            guardrail_flags["output"] = output_flags
        else:
            guarded_output = raw_output

        # 5. Citation binding
        if self.citation_binder and context_chunks:
            result_with_citations = self.citation_binder.bind(
                guarded_output, context_chunks
            )
        else:
            result_with_citations = guarded_output

        # 6. Postprocessor (optional, per-scenario)
        final_output = result_with_citations
        if postprocessor:
            final_output = await postprocessor(
                result_with_citations, config, context_chunks
            )

        # 7. Evaluation (async, non-blocking)
        if self.evaluator and config.eval_metrics:
            _schedule_background_task(
                self.evaluator.evaluate(
                    output=final_output,
                    query=guarded_input,
                    sources=context_chunks,
                    metrics=config.eval_metrics,
                    tenant_id=tenant_id,
                ),
                name="pipeline-evaluation",
            )

        latency_ms = int((time.time() - start_time) * 1000)

        # 8. Audit log (async, non-blocking)
        _schedule_background_task(
            _write_audit_async(
                tenant_id=tenant_id,
                user_id=user_id,
                scenario_id=config.scenario_id,
                input_summary=guarded_input,
                output_summary=final_output,
                latency_ms=latency_ms,
                tokens_used=tokens_used,
                confidence=confidence,
                guardrail_flags=guardrail_flags,
                sources=context_chunks,
            ),
            name="pipeline-audit-log",
        )

        # 9. Token budget tracking (async, non-blocking)
        if tokens_used:
            _schedule_background_task(
                _record_token_budget_async(tenant_id, tokens_used),
                name="pipeline-token-budget",
            )

        return PipelineResult(
            output=final_output,
            sources=context_chunks,
            confidence=confidence,
            guardrail_flags=guardrail_flags,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
        )


async def _write_audit_async(
    tenant_id: str,
    user_id: str,
    scenario_id: str,
    input_summary: str,
    output_summary: str,
    latency_ms: int,
    tokens_used: int | None,
    confidence: float,
    guardrail_flags: dict,
    sources: list[dict],
) -> None:
    """Write audit log entry (fire-and-forget)."""
    try:
        from app.shared.audit import write_audit_log

        await write_audit_log(
            tenant_id=tenant_id,
            user_id=user_id,
            scenario_id=scenario_id,
            input_summary=input_summary,
            output_summary=output_summary,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            confidence=confidence,
            guardrail_flags=guardrail_flags,
            sources=sources,
        )
    except Exception as exc:
        logger.error("audit_write_failed", error=str(exc))


def _record_token_budget(tenant_id: str, tokens: int) -> None:
    """Record token usage for budget tracking (kept for compatibility)."""
    try:
        from app.shared.token_budget import record_token_usage
        asyncio.run(record_token_usage(tenant_id, tokens))
    except Exception as exc:
        logger.error("token_budget_record_failed", tenant_id=tenant_id, error=str(exc))


async def _record_token_budget_async(tenant_id: str, tokens: int) -> None:
    """Record token usage for budget tracking (fire-and-forget)."""
    try:
        from app.shared.token_budget import record_token_usage
        await record_token_usage(tenant_id, tokens)
    except Exception as exc:
        logger.error("token_budget_record_failed", tenant_id=tenant_id, error=str(exc))
