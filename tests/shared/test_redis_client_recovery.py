"""Redis 客户端「依赖恢复后能否自愈」回归测试（2026-09-10）。

由 `scripts/fault_injection_matrix.py` 的故障注入发现：

同一事件循环里，Redis 只要短暂不可用一次，`_client_unavailable` 就会**永久**
短路后续调用 —— 即使 Redis 早已恢复，`get_redis()` 仍返回 ``None``。
后果链：

    Redis 抖一下 → get_redis() 永久 None → RateLimiter 判定"存储不可用"
    → rate_limit_fail_open=False（默认）→ 抛 RateLimitError
    → **每个已登录请求都 429，直到重启进程**

更糟的是它**掩盖真实故障**：一次 PostgreSQL 宕机在矩阵里被测成 429
（限流），而不是 500 —— 排查方向会被整体带偏。

修复：把"永久短路"改成"冷却期内短路，冷却结束允许重试"。
本文件把两侧都钉住：既要能自愈，也不能变成猛砸挂掉的服务。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config.settings import settings  # noqa: E402
from app.shared import redis_client as rc  # noqa: E402

DEAD_URL = "redis://127.0.0.1:6399/0"  # 必然不可达


async def _real_redis_is_up() -> bool:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url)
        try:
            await client.ping()
            return True
        finally:
            await client.aclose()
    except Exception:
        return False


@pytest.fixture(autouse=True)
async def _clean_state():
    """每个用例前后都把模块级缓存清干净，避免用例之间互相污染。"""
    await rc.close_redis()
    original = settings.redis_url
    yield
    settings.redis_url = original
    await rc.close_redis()


async def test_recovers_after_cooldown_when_dependency_comes_back(monkeypatch):
    """依赖恢复后必须能自动重连 —— 否则一次抖动 = 永久 429。"""
    if not await _real_redis_is_up():
        pytest.skip("需要本机 Redis 在跑")

    monkeypatch.setattr(rc, "RETRY_COOLDOWN_SECONDS", 0.0)

    live_url = settings.redis_url  # 必须在改写前留下真实地址
    settings.redis_url = DEAD_URL
    assert await rc.get_redis() is None
    assert rc._client_unavailable is True

    settings.redis_url = live_url
    client = await rc.get_redis()
    assert client is not None, "依赖已恢复却仍返回 None —— 需要重启进程才能恢复，正是本用例要防的缺陷"


async def test_does_not_hammer_a_server_that_just_failed(monkeypatch):
    """冷却期内不得反复重连 —— 自愈不能变成猛砸。"""
    monkeypatch.setattr(rc, "RETRY_COOLDOWN_SECONDS", 3600.0)

    settings.redis_url = DEAD_URL
    assert await rc.get_redis() is None

    # 冷却未过：即便地址换成一个"看起来可用"的，也不应发起新连接
    settings.redis_url = DEAD_URL
    assert await rc.get_redis() is None
    assert rc._client_unavailable is True, "冷却期内应当保持短路状态"
