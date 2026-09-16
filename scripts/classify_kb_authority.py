#!/usr/bin/env python
"""把知识库文档的来源级别（``SourceAuthority``）按显式映射写回数据库。

为什么需要它
------------
``documents.authority`` 的默认值是 ``unknown`` —— 刻意的：它表示"没有人声明过"。
从文件名推断来源级别（"看起来像模板"）恰恰是最不可靠的判据，而判错的代价是把
第三方条款当成本单位制度答出去。所以这一步**必须由人给出映射**，脚本只负责
把它准确、可复现地落库，并把"映射与库里对不上"的地方报出来。

安全边界
--------
全程走 ``make_tenant_session``（RLS 生效的租户会话）：
* 只改指定租户、指定知识库的文档；
* 不用 DDL 后门去关 RLS —— 那样能改到别的租户的数据。

用法
----
    .venv/bin/python scripts/classify_kb_authority.py --tenant eval-runner --kb-id policy_kb
    .venv/bin/python scripts/classify_kb_authority.py --tenant t --kb-id kb --dry-run

退出码：0 正常；1 映射与库里对不上（映射过期，或文档被删了没同步映射）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select, update  # noqa: E402

from app.data.database import make_tenant_session  # noqa: E402
from app.data.models.knowledge_base import (  # noqa: E402
    AUTHORITATIVE_AUTHORITIES,
    Document,
    SourceAuthority,
)

DEFAULT_MAP_PATH = ROOT / "docs" / "ops" / "kb-authority-map.json"


def load_map(path: Path) -> dict[str, SourceAuthority]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    documents = payload.get("documents")
    if not isinstance(documents, dict) or not documents:
        raise SystemExit(f"映射文件缺少非空的 documents 段：{path}")

    parsed: dict[str, SourceAuthority] = {}
    for filename, spec in documents.items():
        raw = spec.get("authority") if isinstance(spec, dict) else spec
        try:
            parsed[str(filename)] = SourceAuthority(str(raw))
        except ValueError as exc:
            valid = ", ".join(item.value for item in SourceAuthority)
            raise SystemExit(f"{filename}: 未知的 authority={raw!r}（可用：{valid}）") from exc
    return parsed


def _mark(authority: str) -> str:
    return "可作依据" if authority in AUTHORITATIVE_AUTHORITIES else "不可作依据"


async def apply_map(tenant: str, kb_id: str, mapping: dict[str, SourceAuthority], dry_run: bool) -> int:
    session = await make_tenant_session(tenant)
    try:
        rows = (
            await session.execute(
                select(Document.id, Document.filename, Document.authority).where(
                    Document.kb_id == kb_id
                )
            )
        ).all()
        by_filename = {filename: (doc_id, authority) for doc_id, filename, authority in rows}

        changed: list[tuple[str, str, str]] = []
        for filename, target in sorted(mapping.items()):
            found = by_filename.get(filename)
            if found is None:
                continue
            doc_id, current = found
            if current == target.value:
                continue
            changed.append((filename, current, target.value))
            if not dry_run:
                await session.execute(
                    update(Document).where(Document.id == doc_id).values(authority=target.value)
                )

        print(f"知识库 {kb_id}（租户 {tenant}）：库内 {len(rows)} 份，映射 {len(mapping)} 条")
        if changed:
            print(f"\n{'将修改' if dry_run else '已修改'} {len(changed)} 份：")
            for filename, before, after in changed:
                print(f"  {filename}\n      {before} → {after}  [{_mark(after)}]")
        else:
            print("\n无需修改（映射与库内一致）。")

        # 映射里有、库里没有 → 映射过期或文档被删。这类漂移必须看得见，
        # 否则映射会慢慢变成一份没人敢信的文档。
        stale = sorted(set(mapping) - set(by_filename))
        # 库里有、映射里没有 → 仍是 unknown。不算错（未来导入会自己声明），
        # 但"以为都分类过了"是最容易犯的错，所以要报出来。
        unclassified = sorted(set(by_filename) - set(mapping))

        if stale:
            print(f"\n[映射过期] {len(stale)} 条在库里找不到，请修正映射：")
            for filename in stale:
                print(f"  - {filename}")
        if unclassified:
            print(f"\n[仍为 unknown] {len(unclassified)} 份未被映射（这些不能作为制度依据）：")
            for filename in unclassified:
                print(f"  - {filename}")

        if not dry_run:
            await session.commit()
            # 分布必须**提交后重新查**才算数：上面的 by_filename 是改动前的快照，
            # 拿它统计会报出一个看起来合理但错误的数字（曾经报成 0/11 可作依据，
            # 而实际有 2 份国家法规）。
            after = (
                await session.execute(
                    select(Document.authority, func.count())
                    .where(Document.kb_id == kb_id)
                    .group_by(Document.authority)
                )
            ).all()
            total = sum(count for _, count in after)
            authoritative = sum(
                count for authority, count in after if authority in AUTHORITATIVE_AUTHORITIES
            )
            print(f"\n落库后分布：{ {a: c for a, c in sorted(after)} }")
            print(f"可作依据（国家法规 / 本单位制度）：{authoritative}/{total}")
        return 1 if stale else 0
    finally:
        await session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="按显式映射写回知识库文档的来源级别")
    parser.add_argument("--tenant", required=True, help="租户 id")
    parser.add_argument("--kb-id", required=True, help="知识库 id")
    parser.add_argument("--map", default=str(DEFAULT_MAP_PATH), help="映射文件（JSON）")
    parser.add_argument("--dry-run", action="store_true", help="只显示将要发生的修改，不落库")
    args = parser.parse_args()

    mapping = load_map(Path(args.map))
    return asyncio.run(apply_map(args.tenant, args.kb_id, mapping, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
