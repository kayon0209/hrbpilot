"""HRBP AI Workbench — Culture Content Orchestrator.

Orchestrates the culture content generation flow:
  Keyword Expansion → Culture KB Retrieval → LLM Multi-Channel Generation → Channel Adaptation

Generates 4 versions: news_article, group_notice, employee_story, event_copy.
"""

import json
import re
import time

from app.rag.config_loader import ScenarioConfig, load_scenario_config
from app.rag.llm.orchestrator import LLMOrchestrator
from app.rag.retrieval.retriever import Retriever
from app.scenarios.culture_content.schemas import (
    CultureContentResponse, KeywordExpansionResponse,
)
from app.shared.logger import get_logger

logger = get_logger(__name__)

# In-memory content store
_content_store: dict[str, CultureContentResponse] = {}


# Keyword expansion templates (local quick expansion)
KEYWORD_EXPANSIONS: dict[str, list[str]] = {
    "团队协作": ["协作精神", "跨部门合作", "团队凝聚力", "协作案例", "协作工具", "沟通"],
    "创新": ["创新精神", "技术突破", "创新文化", "创新激励", "专利成果", "创新故事"],
    "责任": ["社会责任", "担当精神", "使命感", "责任意识", "诚信", "承诺"],
    "学习成长": ["职业发展", "培训体系", "学习文化", "成长故事", "知识分享", "导师制"],
    "关怀": ["员工关怀", "福利体系", "健康保障", "家庭友好", "心理健康", "人文关怀"],
    "奋斗": ["奋斗精神", "拼搏故事", "攻坚克难", "自我驱动", "目标达成", "卓越"],
    "公平": ["公平公正", "透明机制", "机会均等", "评价标准", "晋升公平", "分配合理"],
    "服务": ["客户至上", "服务品质", "用户体验", "响应速度", "满意度", "服务创新"],
}


def _extract_json_from_llm_output(output: str) -> dict:
    json_match = re.search(r"\{[\s\S]*\}", output)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    return {}


class CultureContentOrchestrator:
    """Orchestrator for the Culture Content scenario."""

    def __init__(self, config: ScenarioConfig | None = None):
        self.config = config or load_scenario_config("culture_content")
        self.llm = LLMOrchestrator()
        self.retriever = Retriever()

    def expand_keywords(self, keywords: list[str]) -> KeywordExpansionResponse:
        """Expand keywords using local mapping + LLM."""
        expanded = set(keywords)
        categories = {}

        for kw in keywords:
            local_expansions = KEYWORD_EXPANSIONS.get(kw, [])
            expanded.update(local_expansions)
            if local_expansions:
                categories[kw] = local_expansions

        # Add general expansion terms
        expanded.update(["企业文化", "价值观", "员工体验", "组织氛围"])

        return KeywordExpansionResponse(
            original=keywords,
            expanded=list(expanded),
            categories=categories,
        )

    async def generate(
        self,
        keywords: list[str],
        tenant_id: str,
        user_id: str,
        tone: str = "积极向上",
    ) -> CultureContentResponse:
        """Generate 4-channel culture content from keywords."""
        start_time = time.time()

        # 1. Keyword expansion
        expansion = self.expand_keywords(keywords)
        all_keywords = expansion.expanded

        # 2. Culture KB retrieval
        kb_context = []
        if self.config.knowledge_base_id:
            kb_context = await self.retriever.retrieve(
                query=" ".join(all_keywords[:5]),
                kb_id=self.config.knowledge_base_id,
                strategy=self.config.retrieval_strategy,
                top_k=self.config.retrieval_top_k,
                tenant_id=tenant_id,
            )

        # 3. Build prompt context
        context_text = ""
        for chunk in kb_context:
            context_text += f"\n{chunk.get('content', '')}"

        # 4. LLM generation (4-channel content)
        prompt = self.config.prompt_template
        keywords_str = ", ".join(keywords)

        raw_output, tokens_used = await self.llm.generate(
            prompt_template=prompt.replace("{{ keywords }}", keywords_str).replace("{{ content }}", context_text or "暂无文化素材"),
            context=[{"source": "文化素材", "section": "关键词", "content": context_text or "暂无"}],
            query=f"请基于关键词 [{keywords_str}] 生成4个渠道的文化传播内容，基调: {tone}",
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        # 5. Parse response
        parsed = _extract_json_from_llm_output(raw_output)

        result = CultureContentResponse(
            news_article=parsed.get("news_article", ""),
            group_notice=parsed.get("group_notice", ""),
            employee_story=parsed.get("employee_story", ""),
            event_copy=parsed.get("event_copy", ""),
            keywords_used=parsed.get("keywords_used", keywords),
            tone=parsed.get("tone", tone),
            confidence=0.8 if parsed else 0.3,
        )

        latency_ms = int((time.time() - start_time) * 1000)
        logger.info("culture_content_generated", keywords=keywords, latency_ms=latency_ms)

        return result

    def save_content(self, content_id: str, content: CultureContentResponse):
        _content_store[content_id] = content

    def get_content(self, content_id: str) -> CultureContentResponse | None:
        return _content_store.get(content_id)
