"""HRBP AI Workbench — CapabilityPipeline."""

import asyncio
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from app.rag.config_loader import ScenarioConfig
from app.shared.errors import AppError, ExternalServiceError, RateLimitError
from app.shared.logger import get_logger
from app.shared.token_budget import DEFAULT_MONTHLY_BUDGET, get_monthly_usage

logger = get_logger(__name__)

_background_tasks: set[asyncio.Task[Any]] = set()


def get_background_tasks() -> set[asyncio.Task[Any]]:
    return _background_tasks


def _schedule_background_task(coro: Coroutine[Any, Any, Any], *, name: str) -> None:
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
    output: str
    sources: list[dict]
    confidence: float
    retrieval_score: float
    guardrail_flags: dict
    latency_ms: int
    tokens_used: int | None = None


class CapabilityPipeline:
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

    async def _check_token_budget(self, tenant_id: str, estimated_tokens: int) -> None:
        usage = await get_monthly_usage(tenant_id)
        consumed = int(usage.get("total_tokens", 0))
        if consumed + estimated_tokens > DEFAULT_MONTHLY_BUDGET:
            raise RateLimitError("Token budget exceeded")

    async def execute(
        self, input: str, config: ScenarioConfig, tenant_id: str, user_id: str, preprocessor=None, postprocessor=None
    ) -> PipelineResult:
        start_time = time.time()
        guardrail_flags = {}
        processed_input = input
        if preprocessor:
            processed_input = await preprocessor(input, config)

        if self.input_guard and config.guardrail_rules.input:
            guarded_input, input_flags = await self.input_guard.check(processed_input, config.guardrail_rules.input)
            guardrail_flags["input"] = input_flags
            if input_flags.get("blocked"):
                return PipelineResult(
                    output=input_flags.get("block_message", "输入被护栏拦截"),
                    sources=[],
                    confidence=0.0,
                    retrieval_score=0.0,
                    guardrail_flags=guardrail_flags,
                    latency_ms=int((time.time() - start_time) * 1000),
                )
        else:
            guarded_input = processed_input

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

        retrieval_score = max((chunk.get("confidence", 0.0) for chunk in context_chunks), default=0.0)
        estimated_tokens = max(
            256,
            len(guarded_input) // 2
            + sum(len(str(chunk.get("content", ""))) for chunk in context_chunks) // 4
            + config.max_tokens,
        )
        await self._check_token_budget(tenant_id, estimated_tokens)

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

        if self.output_guard and config.guardrail_rules.output:
            guarded_output, output_flags = await self.output_guard.check(
                raw_output, config.guardrail_rules.output, sources=context_chunks
            )
            guardrail_flags["output"] = output_flags
        else:
            guarded_output = raw_output

        result_with_citations = (
            self.citation_binder.bind(guarded_output, context_chunks)
            if self.citation_binder and context_chunks
            else guarded_output
        )
        final_output = (
            await postprocessor(result_with_citations, config, context_chunks)
            if postprocessor
            else result_with_citations
        )

        if self.evaluator and config.eval_metrics:
            _schedule_background_task(
                self.evaluator.evaluate(
                    output=final_output,
                    query=guarded_input,
                    sources=context_chunks,
                    metrics=config.eval_metrics,
                    tenant_id=tenant_id,
                    scenario_id=config.scenario_id,
                ),
                name="pipeline-evaluation",
            )

        latency_ms = int((time.time() - start_time) * 1000)
        _schedule_background_task(
            _write_audit_async(
                tenant_id=tenant_id,
                user_id=user_id,
                scenario_id=config.scenario_id,
                input_summary=guarded_input,
                output_summary=final_output,
                latency_ms=latency_ms,
                tokens_used=tokens_used,
                retrieval_score=retrieval_score,
                guardrail_flags=guardrail_flags,
                sources=context_chunks,
            ),
            name="pipeline-audit-log",
        )
        if tokens_used:
            _schedule_background_task(_record_token_budget_async(tenant_id, tokens_used), name="pipeline-token-budget")

        return PipelineResult(
            output=final_output,
            sources=context_chunks,
            confidence=retrieval_score,
            retrieval_score=retrieval_score,
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
    retrieval_score: float,
    guardrail_flags: dict,
    sources: list[dict],
) -> None:
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
            confidence=retrieval_score,
            guardrail_flags=guardrail_flags,
            sources=sources,
        )
    except Exception as exc:
        logger.error("audit_write_failed", error=str(exc))


async def _record_token_budget_async(tenant_id: str, tokens: int) -> None:
    try:
        from app.shared.token_budget import record_token_usage

        await record_token_usage(tenant_id, tokens)
    except Exception as exc:
        logger.error("token_budget_record_failed", tenant_id=tenant_id, error=str(exc))
