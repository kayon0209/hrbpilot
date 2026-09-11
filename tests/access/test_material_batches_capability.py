"""批量材料接口的能力门回归测试（2026-09-10 P0）。

背景：`POST /api/material-batches` 原先叠了两个真实缺陷：

1. 函数体第一行 `from app.access.middleware.rbac import _role_has_capability`
   —— 该名字在 `rbac.py` 里根本不存在（只有 `ROLE_CAPABILITIES` 与 `_forbidden`），
   于是每次请求都抛 ImportError → 端点恒 500，500 条批量入库这条链路实际不可用；
2. 紧随其后的 `try: ... raise ValidationError(...)  except Exception: pass`
   会把**自己刚抛出的权限拒绝**一起吞掉 —— 即使修好导入，能力门也形同虚设，
   任何已登录角色都能触发 500 条面谈/声音分析（消耗 LLM 预算）。

另外 `/api/material-batches` 并不在 RBACMiddleware 的 `ROUTE_CAPABILITY_MAP` 里，
所以 handler 内的这道检查是唯一的一道门。本文件把「拒绝」和「放行」两侧都钉住。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.access.routes import material_batches
from app.access.routes.material_batches import BatchItem, CreateBatchBody
from app.shared.errors import ForbiddenError

_CONTENT = "测试面谈纪要正文：" + "员工反馈工作强度与流程效率。" * 4


def _request(role: str) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/api/material-batches", "headers": []})
    request.state.user_id = "u1"
    request.state.tenant_id = "t1"
    request.state.user_role = role
    return request


def _body(kind: str = "interview") -> CreateBatchBody:
    return CreateBatchBody(type=kind, items=[BatchItem(content=_CONTENT)])


@pytest.mark.parametrize("role", ["employee", "admin"])
async def test_batch_create_is_forbidden_without_capability(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    """employee 无面谈分析能力；admin 按设计也不持有业务内容能力。"""

    async def _must_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("能力校验失败时不应创建批次")

    monkeypatch.setattr(material_batches, "create_batch", _must_not_run)

    with pytest.raises(ForbiddenError):
        await material_batches.post_batch(_body(), _request(role))


async def test_batch_create_reaches_service_with_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _create_batch(tenant_id: str, user_id: str, batch_type: str, items: list[dict]):
        captured.update(tenant_id=tenant_id, user_id=user_id, batch_type=batch_type, items=items)
        return SimpleNamespace(id="batch-1", type=batch_type, total=1, completed=0, failed=0, status="queued")

    monkeypatch.setattr(material_batches, "create_batch", _create_batch)

    result = await material_batches.post_batch(_body(), _request("hrbp"))

    assert result["batch_id"] == "batch-1"
    assert captured["tenant_id"] == "t1"
    assert captured["batch_type"] == "interview"
    assert len(captured["items"]) == 1


async def test_voice_batch_requires_voice_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _must_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("能力校验失败时不应创建批次")

    monkeypatch.setattr(material_batches, "create_batch", _must_not_run)

    with pytest.raises(ForbiddenError):
        await material_batches.post_batch(_body("voice"), _request("employee"))


@pytest.mark.parametrize("role", ["employee", "admin"])
async def test_retry_failed_is_forbidden_without_capability(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    """重试会把失败子项重新入队 Celery（重新消耗 LLM），同样必须过能力门。"""

    async def _get_batch(tenant_id: str, batch_id: str):
        return SimpleNamespace(id=batch_id, tenant_id=tenant_id, type="interview")

    monkeypatch.setattr(material_batches, "get_batch", _get_batch)

    with pytest.raises(ForbiddenError):
        await material_batches.retry_failed("batch-1", _request(role))
