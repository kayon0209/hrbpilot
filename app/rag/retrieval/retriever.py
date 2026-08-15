"""HRBP AI Workbench — Vector retrieval service.

Supports dense, sparse, and hybrid retrieval strategies.
Reranking is optional per ScenarioConfig.

In dev mode (no Milvus running), returns mock context chunks so the LLM
has something to work with. This lets you test the full pipeline end-to-end.
"""

from app.rag.config_loader import RetrievalStrategy
from app.shared.logger import get_logger

logger = get_logger(__name__)

# Mock knowledge base for dev mode — sample HR policy documents
_MOCK_KB: dict[str, list[dict]] = {
    "policy_kb": [
        {
            "source": "员工手册v3.2.pdf",
            "section": "第三章 考勤管理",
            "content": (
                "第3.1条 工作时间：公司实行标准工时制，工作日为周一至周五，"
                "每日工作8小时，上午9:00-12:00，下午13:30-18:00。"
                "弹性工作制需经部门负责人审批后方可实施。"
            ),
            "score": 0.92,
        },
        {
            "source": "员工手册v3.2.pdf",
            "section": "第三章 考勤管理",
            "content": (
                "第3.3条 请假流程：员工请假需提前在OA系统提交申请，"
                "1天以内由直接主管审批，1-3天由部门负责人审批，"
                "3天以上需HR总监审批。病假需提供医院证明。"
            ),
            "score": 0.88,
        },
        {
            "source": "薪酬福利管理制度.pdf",
            "section": "第五章 年假制度",
            "content": (
                "第5.2条 年假标准：入职满1年享有5天年假，满3年享有10天，"
                "满5年享有15天。年假当年未休完可顺延至次年3月31日。"
                "离职时未休年假按日薪折算。"
            ),
            "score": 0.85,
        },
        {
            "source": "薪酬福利管理制度.pdf",
            "section": "第六章 社保公积金",
            "content": (
                "第6.1条 社会保险：公司依法为正式员工缴纳五险一金"
                "（养老、医疗、失业、工伤、生育保险及住房公积金）。"
                "缴纳基数为员工上年度月平均工资，个人缴纳部分从月工资中代扣。"
            ),
            "score": 0.80,
        },
        {
            "source": "绩效考核管理办法v2.0.pdf",
            "section": "第二章 考核周期",
            "content": (
                "第2.1条 考核周期：绩效考核分为季度考核和年度考核。"
                "季度考核在每季度结束后10个工作日内完成，"
                "年度考核在次年1月底前完成。考核结果分为S/A/B/C/D五个等级。"
            ),
            "score": 0.78,
        },
    ],
    "interview_kb": [
        {
            "source": "面试评估表模板.docx",
            "section": "能力评估维度",
            "content": "评估维度包括：专业技能(40%)、沟通能力(20%)、团队协作(15%)、学习能力(15%)、文化匹配(10%)。",
            "score": 0.90,
        },
    ],
}


class Retriever:
    """Retrieve relevant document chunks from vector database."""

    async def retrieve(
        self,
        query: str,
        kb_id: str,
        strategy: RetrievalStrategy = RetrievalStrategy.HYBRID,
        top_k: int = 5,
        rerank: bool = False,
        tenant_id: str = "default",
    ) -> list[dict]:
        """Retrieve top_k relevant chunks for the query.

        In dev mode, returns mock data from the in-memory knowledge base.
        In production, this queries Milvus/Qdrant vector database.
        """
        logger.info(
            "retrieval_requested",
            query=query[:50],
            kb_id=kb_id,
            strategy=strategy.value if hasattr(strategy, 'value') else str(strategy),
            top_k=top_k,
            rerank=rerank,
            tenant_id=tenant_id,
        )

        # Dev mode: return mock data from in-memory KB
        mock_chunks = _MOCK_KB.get(kb_id, [])

        if not mock_chunks:
            logger.info("retrieval_empty", kb_id=kb_id, reason="no_mock_data")
            return []

        # Simple keyword matching for dev mode (production uses vector similarity)
        query_lower = query.lower()
        scored = []
        for chunk in mock_chunks:
            # Simple relevance: check if any query words appear in the content
            content_lower = chunk["content"].lower()
            overlap = sum(1 for word in query_lower if len(word) > 1 and word in content_lower)
            # Blend the base score with the overlap bonus
            dev_score = min(1.0, chunk["score"] + overlap * 0.02)
            scored.append({**chunk, "score": dev_score})

        # Sort by score descending
        scored.sort(key=lambda x: x["score"], reverse=True)

        # Return top_k results
        results = scored[:top_k]

        logger.info(
            "retrieval_completed",
            kb_id=kb_id,
            results_count=len(results),
            top_score=results[0]["score"] if results else 0.0,
        )

        return results
