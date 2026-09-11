"""HRBPilot 依赖降级矩阵 —— 故障注入实测脚本（2026-09-10）。

对 Redis / PostgreSQL / 模型服务逐个注入「不可用」，记录系统在真实路径上的表现：

  1. liveness        ``GET /api/health``
  2. readiness       ``GET /api/ready``（逐依赖状态，且不泄漏内部拓扑）
  3. 未鉴权业务读    ``GET /api/notifications``（无 Authorization）
  4. 已鉴权业务读    ``GET /api/notifications``（hrbp JWT）
  5. 模型服务不可达  ``LLMOrchestrator.generate`` 指向不可达 base_url（子进程）

**本脚本只记录真实行为，不判定好坏。** 降级策略的取舍（可用性 vs 安全）
由人来做：fail-closed 的代价是可用性、收益是不会绕过鉴权；fail-open 反之。
输出用于交付文档「降级矩阵」章节取数 —— 不是推断，是跑出来的。

用法::

    python scripts/fault_injection_matrix.py              # 完整矩阵（会 stop/start 容器）
    python scripts/fault_injection_matrix.py --no-docker  # 只测当前依赖状态，不动容器
    python scripts/fault_injection_matrix.py --json out.json

安全：任何异常路径都会把停掉的容器重新拉起。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Windows 上 Docker Desktop 默认不把自己的 bin 目录加进 PATH。
# 需要时设 DOCKER_BIN 指向它；本仓库不写死任何本机路径。
_DOCKER_BIN = os.environ.get("DOCKER_BIN", "").strip()
if _DOCKER_BIN and _DOCKER_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _DOCKER_BIN + os.pathsep + os.environ.get("PATH", "")

CONTAINERS = {
    "redis": "hrbpilot-redis-1",
    "database": "hrbpilot-postgres-1",
    # milvus / minio / etcd 本机未部署，脚本会把它们当前状态如实记录为 "未部署"
}

PROBE_PATH = "/api/notifications"
MODEL_UNREACHABLE_URL = "http://127.0.0.1:9/v1"  # 必然 connection refused


# --------------------------------------------------------------------------- #
# 容器控制
# --------------------------------------------------------------------------- #
def _docker(*args: str) -> tuple[int, str]:
    try:
        out = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=120, shell=False
        )
        return out.returncode, (out.stdout or "") + (out.stderr or "")
    except Exception as exc:  # pragma: no cover - 环境相关
        return -1, str(exc)


def _container_state(name: str) -> str:
    code, out = _docker("inspect", "--format", "{{.State.Running}}", name)
    if code != 0:
        return "not-found"
    return "running" if out.strip() == "true" else "stopped"


def _stop(name: str) -> bool:
    code, _ = _docker("stop", name)
    return code == 0


def _start(name: str) -> bool:
    code, out = _docker("start", name)
    if code == 0 and name.endswith("postgres-1"):
        _wait_pg_healthy(name)
    return code == 0


def _wait_pg_healthy(name: str, attempts: int = 12) -> None:
    for _ in range(attempts):
        code, out = _docker("inspect", "--format", "{{.State.Health.Status}}", name)
        if code == 0 and out.strip() == "healthy":
            return
        time.sleep(5)


# --------------------------------------------------------------------------- #
# 探针
# --------------------------------------------------------------------------- #
def _prepare_env() -> None:
    """把进程环境调到「生产形态 + 可反复建连」。

    两条都是刻意的，原因见 ``app/data/database.py``：

    * ``PYTEST_CURRENT_TEST`` 会让引擎使用 ``NullPool``。asyncpg 连接是**绑定事件循环**
      的，而本脚本会在同一进程里横跨"依赖可用→不可用→恢复"三个阶段；
      若复用池中旧连接，第二阶段之后拿到的会是死连接，测出来的就不是降级行为了。
    * ``APP_DEBUG=false`` 是生产默认：debug 会把 traceback 回显到响应体，
      掩盖真实的降级表现。降级矩阵必须按生产形态测。
    """
    os.environ.setdefault("PYTEST_CURRENT_TEST", "fault_injection_matrix")
    os.environ["APP_DEBUG"] = "false"


def _make_client():
    """进程内 ASGI 客户端：不占端口、不受代理影响，中间件栈与真实请求一致。

    必须用 ``with`` 持有：TestClient 每次请求单独开关事件循环，
    脱离上下文管理器使用会让 DB 连接跨循环失效（首版实测踩到：
    ``AttributeError: 'NoneType' object has no attribute 'send'``）。
    """
    from app.main import app  # noqa: PLC0415
    from fastapi.testclient import TestClient

    return TestClient(app)


def _make_token(role: str = "hrbp") -> str:
    import datetime

    from app.access.routes.auth import _create_access_token

    return _create_access_token("fi-user", role, "fi-tenant", "fi@example.com")


def probe(client, token: str) -> dict:
    """对一组固定路径取真实响应。"""
    result: dict = {}

    def _get(path: str, headers: dict | None = None) -> tuple[int, object]:
        try:
            resp = client.get(path, headers=headers or {})
        except Exception as exc:  # 连接层异常也要如实记录
            return -1, f"{type(exc).__name__}: {exc}"
        try:
            body = resp.json()
        except Exception:
            body = (resp.text or "")[:200]
        return resp.status_code, body

    code, body = _get("/api/health")
    result["liveness"] = {"status": code}

    code, body = _get("/api/ready")
    checks = body.get("checks") if isinstance(body, dict) else None
    result["readiness"] = {
        "status": code,
        "overall": body.get("status") if isinstance(body, dict) else None,
        "critical_failed": body.get("critical_failed") if isinstance(body, dict) else None,
        "optional_unavailable": body.get("optional_unavailable") if isinstance(body, dict) else None,
        "checks": {k: v.get("status") for k, v in checks.items()} if isinstance(checks, dict) else None,
    }

    code, body = _get(PROBE_PATH)
    result["unauthenticated_read"] = {"status": code}

    code, body = _get(PROBE_PATH, {"Authorization": f"Bearer {token}"})
    result["authenticated_read"] = {"status": code}

    return result


def probe_model_unreachable() -> dict:
    """子进程里把 LLM base_url 指向必然拒绝的地址，观察真实异常类型与是否降级。"""
    snippet = f'''# 自动生成：模型服务不可达探测
import asyncio, sys, time
sys.path.insert(0, r"{ROOT}")

from app.rag.llm.orchestrator import LLMOrchestrator


async def main():
    o = LLMOrchestrator()
    t0 = time.time()
    try:
        r = await o.generate("你是一个助手", [], "ping", max_tokens=8)
        print("NO_RAISE", str(r)[:150])
    except BaseException as e:
        print("RAISED", type(e).__name__, str(e)[:200])
    print("ELAPSED", round(time.time() - t0, 1), "s")


asyncio.run(main())
'''
    probe_file = Path(tempfile.gettempdir()) / "hrbp_model_unreachable_probe.py"
    probe_file.write_text(snippet, encoding="utf-8")

    env = dict(os.environ, LLM_BASE_URL=MODEL_UNREACHABLE_URL)
    # 本机有 HTTP(S)_PROXY：不绕开的话，发往 127.0.0.1:9 的请求会被代理接走并
    # 返回 503 "upstream connect failed"，测到的就不是"服务不可达"而是"代理不通"。
    env["NO_PROXY"] = env["no_proxy"] = "*"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(key, None)
    try:
        out = subprocess.run(
            [sys.executable, str(probe_file)],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
            cwd=str(ROOT),
        )
        text = (out.stdout or "") + (out.stderr or "")
    except subprocess.TimeoutExpired:
        text = "TIMEOUT 180s（未设置请求超时会一直挂着 —— 这本身就是一个降级缺陷）"

    text = text.strip()
    # 没打到标记行 = 没测出结论。宁可写"未测得"，也不要拿半截日志编一个行为出来。
    conclusive = ("RAISED" in text) or ("NO_RAISE" in text)
    if conclusive:
        # 只留结论行：从头部截断会把答案切掉（首版就踩了这个坑，
        # 输出里只剩半句 `llm_call_failed error='Connectio`）。
        text = "\n".join(
            ln for ln in text.splitlines() if ("RAISED" in ln or "NO_RAISE" in ln or "ELAPSED" in ln)
        )
    return {
        "base_url": MODEL_UNREACHABLE_URL,
        "conclusive": conclusive,
        "output": text if conclusive else text[-600:],
    }


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def _fmt(v) -> str:
    return "—" if v is None else str(v)


def _status(v) -> str:
    """-1 表示请求把异常抛穿了（TestClient 不吞异常）。

    真实部署里这些异常会被全局 error handler 转成 5XX —— 所以在矩阵里
    它代表的是"故障信号没有被伪装成别的东西"，而不是"没有响应"。
    """
    if v is None:
        return "—"
    if v == -1:
        return "**异常抛出**（部署态由 error handler 转 5XX）"
    return str(v)


def _readiness_cell(readiness: dict) -> str:
    """readiness 单元格：总体状态 + HTTP 码。

    HTTP 码现在是语义的一部分（2026-09-11 起）：200 = 可服务，503 = 关键依赖
    故障、应从流量里摘除。只写总体状态会丢掉"编排系统到底会不会摘它"这层信息。
    """
    overall = readiness.get("overall")
    code = readiness.get("status")
    if overall is None:
        return "—"
    cell = f"`{overall}`"
    if code is not None:
        cell += f"（HTTP {code}）"
    critical = readiness.get("critical_failed") or []
    if critical:
        cell += f"<br>关键：{', '.join(critical)}"
    return cell


def render_markdown(rows: list[dict], model_probe: dict | None) -> str:
    lines = [
        "# HRBPilot 依赖降级矩阵（故障注入实测）",
        "",
        "> 由 `scripts/fault_injection_matrix.py` 实际停/起容器后探测得出，非推断。",
        f"> 探测路径：liveness `/api/health`、readiness `/api/ready`、业务读 `{PROBE_PATH}`。",
        "",
        "## 1. 依赖不可用时的系统行为",
        "",
        "| 故障场景 | liveness | readiness 总体 | 未鉴权读 | 已鉴权读 | 判定 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            "| {name} | {lv} | {rd} | {ua} | {au} | {verdict} |".format(
                name=r["scenario"],
                lv=_fmt(r["probe"]["liveness"]["status"]),
                rd=_readiness_cell(r["probe"]["readiness"]),
                ua=_status(r["probe"]["unauthenticated_read"]["status"]),
                au=_status(r["probe"]["authenticated_read"]["status"]),
                # 每次渲染都重算，避免改了判定逻辑后旧 JSON 里的结论变成过期文本
                verdict=_verdict(r["scenario"], r["probe"]),
            )
        )

    lines += [
        "",
        "## 2. readiness 逐依赖状态",
        "",
        "依赖按**影响半径**分两级（2026-09-11 起），状态用词也随级别不同：",
        "",
        "| 级别 | 依赖 | 不可用时状态 | 对整体的影响 |",
        "| --- | --- | --- | --- |",
        "| critical | database, redis | `error` | `not_ready` + HTTP 503（应从流量摘除） |",
        "| optional | milvus, minio, embedding | `unavailable` | `degraded` + HTTP 200（仍可服务） |",
        "",
    ]
    deps = sorted({d for r in rows for d in (r["probe"]["readiness"]["checks"] or {})})
    lines.append("| 场景 | " + " | ".join(deps) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in deps) + " |")
    for r in rows:
        checks = r["probe"]["readiness"]["checks"] or {}
        lines.append("| " + r["scenario"] + " | " + " | ".join(_fmt(checks.get(d)) for d in deps) + " |")

    if model_probe:
        lines += [
            "",
            "## 3. 模型服务不可达",
            "",
            f"注入方式：`LLM_BASE_URL={model_probe['base_url']}`（必然 refused，且绕过本机 HTTP 代理）",
            "",
        ]
        if model_probe.get("conclusive"):
            lines += ["```", model_probe["output"], "```"]
        else:
            lines += [
                "**未测得。** 子进程未返回可判定结果（只拿到 `llm_call_failed` 半截日志便中断）。",
                "",
                "已知干扰因素：",
                "",
                "- active provider 存在 Redis 里（`llm:active_provider`），不随 `LLM_BASE_URL` 切换；",
                "- 本机有 HTTP(S)_PROXY，未绕开时请求会被代理接走并报 503 `upstream connect failed`。",
                "",
                "原始输出尾部：",
                "",
                "```",
                model_probe["output"],
                "```",
                "",
                "> 这一行保持「未测得」而不是填一个看起来合理的结论 —— 降级矩阵里"
                "编造一格，和编造用户反馈是同一类错误。",
            ]
    lines += [
        "",
        "## 4. 读数注意事项",
        "",
        "1. **未鉴权请求在任何依赖故障下仍是 401**：中间件顺序是 "
        "`Auth → RBAC → RateLimit → Tenant`，鉴权先于限流。"
        "所以限流 fail-closed 的代价是**可用性**，不是安全性 —— 它不会让任何人绕过鉴权。",
        "2. **readiness 是分级的，别只看「degraded 就是坏了」**：关键依赖故障 ⇒ "
        "`not_ready` + HTTP 503；仅可选依赖不可用 ⇒ `degraded` + HTTP 200，"
        "服务照常工作。修复前（2026-09-11 前）两种情况的权重相同，"
        "导致 milvus/minio 一旦没装，`/api/ready` 就恒为 `degraded` —— "
        "拿去做 K8s readinessProbe 会让服务永远不被判定为就绪。**分级后这个坑已消除。**",
        "3. **区分「可选依赖没装」与「关键依赖挂了」靠 `critical_failed` 字段**，"
        "不要靠总体状态猜：`critical_failed` 为空 = 服务仍可用。",
        "4. **表格里的「异常抛出」是好事**：它代表故障信号没有被伪装成别的错误。"
        "2026-09-10 修复前，PostgreSQL 宕机被测成 **429（限流）** —— 因为 Redis "
        "客户端一旦失败就在同一事件循环内永久短路，限流器持续 fail-closed。"
        "排查方向会被整体带偏。修复后这里变成异常抛出，信号才对得上故障。",
        "",
        "---",
        "",
        "*由脚本自动生成，请勿手改；改行为请改代码后重跑。*",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="HRBPilot 依赖故障注入矩阵")
    parser.add_argument("--no-docker", action="store_true", help="不启停容器，只测当前状态")
    parser.add_argument("--json", dest="json_path", default=None, help="额外输出 JSON 报告")
    parser.add_argument("--skip-model", action="store_true", help="跳过模型不可达探测（较慢）")
    parser.add_argument("--only-model", action="store_true", help="只跑模型不可达探测")
    parser.add_argument("--from-json", dest="from_json", default=None, help="用上次跑出的 JSON 重新渲染 Markdown（不动容器）")
    args = parser.parse_args()

    if args.from_json:
        data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        out_md = ROOT / "docs" / "operations" / "dependency-failure-matrix.md"
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(render_markdown(data["rows"], data.get("model_probe")), encoding="utf-8")
        print(f"已写入 {out_md}")
        return 0

    if args.only_model:
        # 只测模型不可达：不碰容器、不起应用，避免把一次快速探测变成 3 分钟的
        # 全量注入（--only-model 的早退必须在场景循环之前）。
        print(probe_model_unreachable()["output"])
        return 0

    _prepare_env()
    try:
        token = _make_token()
    except Exception as exc:  # pragma: no cover
        print(f"无法签发测试 token：{exc}", file=sys.stderr)
        token = ""

    scenarios: list[tuple[str, tuple[str, ...]]] = [
        ("基线（依赖全可用）", ()),
        ("Redis 不可用", ("redis",)),
        ("PostgreSQL 不可用", ("database",)),
        ("Redis + PostgreSQL 同时不可用", ("redis", "database")),
    ]

    rows: list[dict] = []
    try:
        with _make_client() as client:  # 单一事件循环贯穿全部场景
            for name, down in scenarios:
                if not args.no_docker:
                    for key in down:
                        _stop(CONTAINERS[key])
                    for key in ("redis", "database"):
                        if key not in down and _container_state(CONTAINERS[key]) != "running":
                            _start(CONTAINERS[key])
                    # 等依赖真正可用/不可用。注意：Redis 客户端有 10s 重连冷却
                    # （app/shared/redis_client.py），等太短会把"尚未自愈"误记成"不能自愈"。
                    time.sleep(12)

                p = probe(client, token)
                verdict = _verdict(name, p)
                rows.append({"scenario": name, "down": list(down), "probe": p, "verdict": verdict})
                print(f"[done] {name} → {json.dumps(p, ensure_ascii=False)}", flush=True)
    finally:
        if not args.no_docker:
            for key in ("redis", "database"):
                if _container_state(CONTAINERS[key]) != "running":
                    print(f"恢复容器：{CONTAINERS[key]}", flush=True)
                    _start(CONTAINERS[key])

    model_probe = None if args.skip_model else probe_model_unreachable()

    md = render_markdown(rows, model_probe)
    out_md = ROOT / "docs" / "operations" / "dependency-failure-matrix.md"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    print(f"\n已写入 {out_md}")

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps({"rows": rows, "model_probe": model_probe}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已写入 {args.json_path}")
    return 0


def _verdict(name: str, p: dict) -> str:
    """把实测结果翻译成一句话结论（人可读，但仍基于实测状态码）。"""
    au = p["authenticated_read"]["status"]
    ua = p["unauthenticated_read"]["status"]
    if "基线" in name:
        return "正常"
    if ua not in (401, 403):
        return f"鉴权 {ua}（异常，需人工确认）"
    if au == 429:
        return "鉴权仍生效(401)，已登录请求 429 → 限流 fail-closed"
    if au == -1:
        return "鉴权仍生效(401)，已登录请求抛异常 → 故障信号未被伪装"
    return f"鉴权仍生效(401)，已登录请求 {au}"


if __name__ == "__main__":
    raise SystemExit(main())
