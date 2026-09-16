#!/usr/bin/env python
"""把 kb_docs/ 下的制度文档批量导入知识库（走生产 ingestion 路径）。

为什么必须有它
--------------
``kb_docs/`` 在 ``README.md`` 里被声明为"HR 知识库原始文档"，但**全仓没有任何代码
引用它** —— 把文件放进去是惰性的：不上传对象存储、不切分、不向量化，
``documents`` / ``document_chunks`` / ``knowledge_bases`` 三张表永远是 0 行，
于是 ``search_policy`` 对任何问题都只回 ``NO_KNOWLEDGE_BASE``。
本脚本补上这条缺失的通道：遍历目录 → 过滤可入库格式 → MinIO → DocumentParser →
section Chunker → EmbeddingClient → PostgreSQL + Milvus。

三条它刻意守住的不变量
----------------------
1. **不静默跳过**。``app/rag/ingestion/pipeline.py`` 只接受 ``txt/pdf/docx``，
   其余扩展名一律进 ``rejected`` 清单：结束时打印、并落进 manifest 文件。
   "文件存在"曾被当成"知识可用"（123 个文件里只有 10 个能入库），
   静默丢弃会让同一个错觉重演一次。
2. **不重复入库**。``documents`` 表上有 ``(kb_id, content_sha256)`` 唯一索引，
   同一份内容第二次运行走 ``skipped_duplicate`` 而不是抛唯一冲突。
3. **不删既有数据**。知识库已存在时只复用并确保 ``status='active'``，
   绝不 delete（与 ``evaluation/seed_eval_kb.py`` 的"整库替换"语义相反：
   那是评测语料，这是一份需要累积的制度库）。

一个必须知道的跨租户约束
------------------------
``uq_documents_kb_content_sha256_nonempty`` **曾经**建在 ``(kb_id, content_sha256)``
上、不含 ``tenant_id``，于是同一个 ``kb_id`` 下两个租户导入同一个文件会撞唯一索引。
已于 2026-09-16 由迁移 ``044_documents_tenant_scoped_dedup`` 修正为
``(tenant_id, kb_id, content_sha256)`` —— 去重现在按租户作用域，与下面这条查询的
实际语义一致。多租户导入不再需要给每个租户分配不同的 ``--kb-id``。

用法
----
    # 先空跑，只看会导入什么、会拒绝什么
    .venv/bin/python scripts/import_kb_docs.py --tenant eval-runner --dry-run

    # 真导入
    .venv/bin/python scripts/import_kb_docs.py --tenant eval-runner --kb-id policy_kb

前置：postgres / redis / minio / milvus / etcd 已起；``EMBEDDING_BASE_URL`` 与
``EMBEDDING_API_KEY``（或 ``LLM_API_KEY``）已在 .env 配好 —— 缺 base_url 时
``AsyncOpenAI`` 会静默回落到 api.openai.com 并 401。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import suppress
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.data.database import make_tenant_session  # noqa: E402
from app.data.models.knowledge_base import (  # noqa: E402
    Document,
    DocumentChunk,
    KnowledgeBase,
    SourceAuthority,
)
from app.rag.embedding import get_embedder  # noqa: E402
from app.rag.ingestion.pipeline import SUPPORTED_TYPES, IngestionService  # noqa: E402
from app.rag.storage.milvus import MilvusStore  # noqa: E402
from app.rag.storage.object_store import ObjectStore  # noqa: E402

CONTENT_TYPES = {
    "txt": "text/plain",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Office 模板/表单类扩展名：能转换也不该进"制度库"。分开报，
# 是为了让"该转格式"和"本来就不该入库"两种处置不被混成一个数字。
FORM_TEMPLATE_EXTENSIONS = {"xls", "xlsx", "ppt", "pptx", "csv", "dot"}


def file_type_of(path: Path) -> str:
    """Normalise the extension into the pipeline's file_type vocabulary."""
    return path.suffix.lower().lstrip(".")


def discover_documents(root: Path) -> tuple[list[Path], list[dict[str, str]]]:
    """Split a document tree into (importable, rejected).

    ``rejected`` carries a machine-readable reason per file instead of dropping
    them, because the whole point of this script is to stop "N files on disk"
    from being read as "N documents searchable".

    Ordering is sorted so two runs produce identical manifests and identical
    ``chunk_index`` assignment — stable chunk ids depend on stable document
    creation order.
    """
    importable: list[Path] = []
    rejected: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or path.name.startswith("."):
            continue
        ft = file_type_of(path)
        if ft in SUPPORTED_TYPES:
            importable.append(path)
            continue
        if ft in FORM_TEMPLATE_EXTENSIONS:
            reason = "表单/课件类，不是制度正文：转换后仍不应进制度库"
        else:
            reason = f"格式不受支持（pipeline 仅接受 {'/'.join(sorted(SUPPORTED_TYPES))}）"
        rejected.append({"path": str(path.relative_to(root)), "ext": ft or "(无扩展名)", "reason": reason})
    return importable, rejected


def s3_key_for(tenant_id: str, kb_id: str, path: Path, root: Path) -> str:
    """Object key = tenant/kb/原始相对路径.

    Keeping the directory structure means two files with the same basename in
    different template packs do not overwrite each other in MinIO.
    """
    return f"{tenant_id}/{kb_id}/{path.relative_to(root).as_posix()}"


def sha256_hex(data: bytes) -> str:
    return sha256(data).hexdigest()


async def ensure_knowledge_base(
    session: Any, tenant_id: str, kb_id: str, name: str, scenario_id: str, chunk_strategy: str
) -> tuple[KnowledgeBase, bool]:
    """Return (kb, created). Reuses an existing KB; never deletes one."""
    existing = (
        await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status != "active":
            existing.status = "active"
            await session.commit()
        return existing, False
    kb = KnowledgeBase(
        id=kb_id,
        tenant_id=tenant_id,
        scenario_id=scenario_id,
        name=name,
        chunk_strategy=chunk_strategy,
    )
    session.add(kb)
    await session.commit()
    return kb, True


async def run(args: argparse.Namespace) -> int:
    root: Path = args.root
    if not root.is_dir():
        print(f"[error] 目录不存在：{root}", file=sys.stderr)
        return 2

    importable, rejected = discover_documents(root)
    print(f"[discover] root={root}")
    print(f"[discover] 可入库 {len(importable)} 个 / 拒绝 {len(rejected)} 个（共 {len(importable) + len(rejected)}）")
    for item in rejected:
        print(f"  [reject] {item['ext']:<8} {item['path']}  ← {item['reason']}")

    if args.dry_run:
        print(
            "\n[dry-run] 不做任何写操作。目标 KB："
            f"tenant={args.tenant} kb_id={args.kb_id} strategy={args.chunk_strategy}"
        )
        for p in importable:
            print(f"  [would-index] {p.relative_to(root)}")
        return 0

    if args.limit:
        importable = importable[: args.limit]

    object_store = ObjectStore()
    await object_store.ensure_bucket_async()
    milvus = MilvusStore()
    await milvus.ensure_collection_async()
    service = IngestionService(milvus=milvus, object_store=object_store)

    indexed: list[dict[str, Any]] = []
    skipped_duplicate: list[str] = []
    failed: list[dict[str, str]] = []

    session = await make_tenant_session(args.tenant)
    try:
        kb, created = await ensure_knowledge_base(
            session, args.tenant, args.kb_id, args.kb_name, args.scenario, args.chunk_strategy
        )
        print(f"[kb] {'创建' if created else '复用'} tenant={args.tenant} id={kb.id} status={kb.status}")

        for path in importable:
            rel = path.relative_to(root).as_posix()
            try:
                payload = path.read_bytes()
                digest = sha256_hex(payload)
                # 唯一索引 (kb_id, content_sha256) 是库级约束：先查一次可以给出
                # "同内容已存在"这一可读结论，而不是抛 IntegrityError 让人猜。
                dup = (
                    await session.execute(
                        select(Document.id).where(
                            Document.kb_id == args.kb_id,
                            Document.tenant_id == args.tenant,
                            Document.content_sha256 == digest,
                        )
                    )
                ).scalar_one_or_none()
                if dup is not None:
                    skipped_duplicate.append(rel)
                    print(f"  [skip-dup] {rel} → 已有 document {dup}")
                    continue

                ft = file_type_of(path)
                s3_key = s3_key_for(args.tenant, args.kb_id, path, root)
                await object_store.put_async(s3_key, payload, content_type=CONTENT_TYPES[ft])
                document = Document(
                    tenant_id=args.tenant,
                    kb_id=args.kb_id,
                    filename=path.name,
                    s3_key=s3_key,
                    file_type=ft,
                    content_type=CONTENT_TYPES[ft],
                    size_bytes=len(payload),
                    content_sha256=digest,
                    status="uploaded",
                    authority=args.authority,
                )
                session.add(document)
                await session.flush()
                await service.process_document(document, session, chunk_strategy=args.chunk_strategy)
                # 记下真实 chunk 数：这是"知识可用"与"文件存在"的唯一分界证据。
                # 0 chunk 的文件必须显式暴露，否则又是一个"看起来导进去了"。
                n_chunks = (
                    await session.execute(
                        select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document.id)
                    )
                ).scalar_one()
                indexed.append(
                    {
                        "path": rel,
                        "document_id": str(document.id),
                        "file_type": ft,
                        "bytes": len(payload),
                        "chunks": int(n_chunks),
                    }
                )
                print(f"  [indexed] {rel} → document {document.id}, {n_chunks} chunks")
            except Exception as e:
                # 单个文件失败不得中断整批：一批 100+ 个文件里坏一个就整体退出，
                # 会让人误以为"导入没跑"，而真正该看的是 manifest 里那一条 failed。
                await session.rollback()
                failed.append({"path": rel, "error": f"{type(e).__name__}: {str(e)[:300]}"})
                print(f"  [FAILED] {rel} → {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)

        if indexed:
            await milvus.flush_async()
    finally:
        await session.close()
        with suppress(Exception):
            await get_embedder().aclose()

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "root": str(root),
        "tenant": args.tenant,
        "kb_id": args.kb_id,
        "kb_name": args.kb_name,
        "scenario_id": args.scenario,
        "chunk_strategy": args.chunk_strategy,
        "counts": {
            "importable": len(importable),
            "indexed": len(indexed),
            "skipped_duplicate": len(skipped_duplicate),
            "rejected": len(rejected),
            "failed": len(failed),
        },
        "indexed": indexed,
        "skipped_duplicate": skipped_duplicate,
        "rejected": rejected,
        "failed": failed,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[summary]")
    for k, v in summary["counts"].items():
        print(f"  {k:<18} {v}")
    print(f"  manifest           {args.manifest}")
    if rejected:
        print(f"\n[注意] {len(rejected)} 个文件未入库（见 manifest 的 rejected 段），它们不可检索。")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="批量导入 kb_docs/ 到知识库（生产 ingestion 路径）")
    p.add_argument("--root", type=Path, default=ROOT / "kb_docs", help="文档根目录（默认 <repo>/kb_docs）")
    p.add_argument("--tenant", required=True, help="租户 id，例如 eval-runner")
    p.add_argument("--kb-id", default="policy_kb", help="知识库 id（跨租户不可同名，见模块 docstring）")
    p.add_argument("--kb-name", default="HR 制度库", help="知识库显示名")
    p.add_argument("--scenario", default="policy_qa", help="场景 id（默认 policy_qa）")
    p.add_argument("--chunk-strategy", default="section", choices=["section", "fixed_512"], help="切分策略")
    p.add_argument(
        "--authority",
        default=SourceAuthority.UNKNOWN.value,
        choices=[item.value for item in SourceAuthority],
        help=(
            "本次导入文档的来源级别（默认 unknown）。"
            "入库时必须声明，因为'能不能解析'与'能不能当依据'是两件事："
            "第三方模板格式完全合法，却不能用来说明'我们公司是怎么规定的'。"
            "漏标可以事后用 scripts/classify_kb_authority.py 补。"
        ),
    )
    p.add_argument("--dry-run", action="store_true", help="只报告会做什么，不写任何数据")
    p.add_argument("--limit", type=int, default=0, help="最多导入前 N 个（调试用）")
    p.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "docs" / "ops" / "kb-docs-import-manifest.json",
        help="导入清单落盘路径（含 rejected/failed 明细）",
    )
    return p


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(build_parser().parse_args())))
