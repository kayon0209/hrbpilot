"""HRBP AI Workbench — Policy QA PreProcessor: Query Rewriting.

Converts colloquial user questions into retrieval-friendly queries.
Examples:
  "年假怎么休" → "年假申请流程 条件 天数 规定"
  "加班费怎么算" → "加班费计算标准 法定节假日 工作日"
"""

from app.rag.config_loader import ScenarioConfig
from app.rag.llm.orchestrator import LLMOrchestrator
from app.shared.logger import get_logger

logger = get_logger(__name__)

# Common colloquial → formal mappings for quick local rewrite
LOCAL_REWRITE_MAP: dict[str, str] = {
    "年假怎么休": "年假申请流程 条件 天数 规定",
    "年假": "年假申请流程 条件 天数 规定",
    "加班费怎么算": "加班费计算标准 法定节假日 工作日 加班",
    "加班": "加班管理规定 加班费 加班审批",
    "报销": "费用报销流程 报销标准 报销凭证",
    "报销流程": "费用报销流程 报销标准 报销凭证 审批",
    "试用期": "试用期规定 试用期时长 试用期考核 转正条件",
    "转正": "转正流程 转正条件 试用期转正评估",
    "离职": "离职流程 离职手续 离职交接 离职补偿",
    "辞职": "离职流程 离职手续 辞职规定",
    "调岗": "调岗申请 内部转岗 调岗流程 调岗条件",
    "调薪": "调薪规定 薪酬调整 调薪流程 调薪标准",
    "婚假": "婚假规定 婚假天数 婚假申请流程",
    "产假": "产假规定 产假天数 产假申请 生育津贴",
    "病假": "病假规定 病假工资 病假流程 医疗期",
    "绩效考核": "绩效考核方案 考核标准 考核周期 考核指标",
    "晋升": "晋升通道 晋升条件 晋升流程 晋升评估",
    "五险一金": "社保缴纳规定 公积金 缴纳基数 缴纳比例",
    "社保": "社保缴纳规定 五险 缴纳基数 缴纳比例",
    "公积金": "公积金规定 缴纳基数 缴纳比例 提取条件",
    "考勤": "考勤管理制度 考勤打卡 考勤异常 考勤处罚",
    "迟到": "考勤迟到规定 迟到处罚 迟到扣款",
    "远程办公": "远程办公规定 居家办公 远程工作申请",
    "培训": "培训管理制度 培训申请 培训预算 培训考核",
    "出差": "出差管理规定 出差审批 出差补贴 出差标准",
    "劳动合同": "劳动合同签订 劳动合同续签 劳动合同变更",
    "保密": "保密协议 保密规定 信息安全 竞业限制",
    "竞业": "竞业限制协议 竞业补偿 竞业期限",
}


async def rewrite_query(query: str, config: ScenarioConfig) -> str:
    """Rewrite a colloquial query into a retrieval-friendly form.

    Strategy:
    1. Check local rewrite map for exact matches
    2. If no match, use LLM for intelligent rewriting (if available)
    3. Fallback to original query
    """
    # Step 1: Local exact match
    if query in LOCAL_REWRITE_MAP:
        rewritten = LOCAL_REWRITE_MAP[query]
        logger.info("query_rewritten_local", original=query, rewritten=rewritten)
        return rewritten

    # Step 2: LLM rewrite (for non-trivial queries)
    try:
        llm = LLMOrchestrator()
        rewrite_prompt = (
            "你是一个 HR 制度检索查询改写助手。"
            "将用户的口语化问题改写为更适合检索的制度关键词组合。"
            "规则:\n"
            "1. 保留原始意图\n"
            "2. 补充相关制度术语和关键词\n"
            "3. 用空格分隔关键词\n"
            "4. 只输出改写后的关键词，不要解释\n\n"
            f"用户问题: {query}"
        )

        result, _ = await llm.generate(
            prompt_template=rewrite_prompt,
            context=[],  # No RAG context for query rewriting
            query=query,
            max_tokens=100,
            temperature=0.0,
        )

        # Clean up: remove quotes, punctuation, extra whitespace
        rewritten = result.strip().strip('"').strip("'").strip("。").strip("，")
        if rewritten and len(rewritten) >= len(query):
            logger.info("query_rewritten_llm", original=query, rewritten=rewritten)
            return rewritten

    except Exception as e:
        logger.warning("query_rewrite_llm_failed", error=str(e), query=query)

    # Step 3: Fallback to original
    logger.info("query_rewrite_fallback", original=query)
    return query
