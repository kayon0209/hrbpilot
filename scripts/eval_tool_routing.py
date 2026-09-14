"""工具路由基线评测 —— 一句中文口语 → 是否选对工具 / 补问 / 拒绝。

这是什么
--------
MCP 工具描述是写给外部 Agent（Codex / WorkBuddy）的路由依据。这份脚本在**服务之外**
模拟一个外部 Agent：读同一份工具目录（名称 + 描述 + 入参 schema），把黄金集里每句
中文话术交给真实 LLM 判断"该调哪个工具 / 该补问 / 该拒绝"，再对判定为"调工具"的
条目**真实调用 MCP 端点**验证那个工具确实可用（带出处/分页/审批信封的返回契约）。

它不在 Server 内再跑一个 LLM —— 评测器是**客户端**，与 Codex 同一位置。

输入
----
- `evaluation/tool_routing_golden.jsonl`：黄金集（expect ∈ tool/clarify/refuse/multi，
  含 held-out 子集 —— 不用于调描述，只用于复测）。
- 真实 MCP 端点（默认 `http://localhost:8001/mcp`）与 AS（`:8002`）。
- 真实 LLM key（复用 env.docker 的 LLM_API_KEY；评测器用它做路由判断）。

输出（stdout 摘要 + JSONL 明细到 evaluation/results/）
----
- 工具路由准确率 / 正确补问率 / 正确拒绝率 / multi 命中率
- 平均工具调用数、输入输出 token（结果侧预算是否生效的观察口）
- train / held-out 分列（held-out 才是泛化数字）
- 失败样例（id / 话术 / 期望 / 实际 / 原因）—— 改描述还是改工具划分的依据

用法
----
    # 容器内（推荐，与门禁同一环境）：
    docker compose --profile test run --rm test \
        python scripts/eval_tool_routing.py --as-url http://oauth-as:8000 --mcp-url http://app:8000/mcp

    # 本机（服务在宿主端口上）：
    python scripts/eval_tool_routing.py

环境变量（凭据只从环境读，不进代码）：
    EVAL_USER_EMAIL / EVAL_USER_PASSWORD / EVAL_TENANT_ID   测试账号（默认 hr-manager）
    LLM_API_KEY / LLM_BASE_URL / LLM_MODEL                  路由判断用的真实模型
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = REPO_ROOT / "evaluation" / "tool_routing_golden.jsonl"
RESULTS_DIR = REPO_ROOT / "evaluation" / "results"

#: 工具目录从服务真实拉取（tools/list），不维护第二份 —— 描述改了评测跟着走。
DEFAULT_MCP_URL = os.environ.get("HRBPILOT_MCP_URL", "http://localhost:8001/mcp")
DEFAULT_AS_URL = os.environ.get("HRBPILOT_AS_URL", "http://localhost:8002")
LOGIN_TENANT = os.environ.get("EVAL_TENANT_ID", "default")
EVAL_EMAIL = os.environ.get("EVAL_USER_EMAIL", "hr-manager@hrbpilot.test")
EVAL_PASSWORD = os.environ.get("EVAL_USER_PASSWORD", "hr-manager-pass")
EVAL_ROLE_HINT = os.environ.get("EVAL_USER_ROLE", "hr_manager")

READ_TOOLS = {
    "search_policy",
    "get_policy_source",
    "search_cases",
    "get_case_summary",
    "get_approval_status",
    "get_my_access_profile",
}

MAX_SAMPLES = int(os.environ.get("EVAL_MAX_SAMPLES", "0"))  # 0 = 全量


@dataclass
class SampleResult:
    id: str
    utterance: str
    expect: str
    expected_tool: str | None
    split: str
    # 判定输出
    predicted_kind: str = ""  # tool / clarify / refuse / multi
    predicted_tools: list[str] = field(default_factory=list)
    followup_mentioned: list[str] = field(default_factory=list)
    refused: bool = False
    invented_data: bool = False
    # 真实调用验证（expect=tool 且模型选了工具时）
    live_call_outcome: str = ""
    live_call_ok: bool | None = None
    # 计量
    input_tokens: int = 0
    output_tokens: int = 0
    tool_call_count: int = 0
    passed: bool = False
    reason: str = ""


# ---------------------------------------------------------------------------
# OAuth：走与真实客户端完全相同的 授权码 + PKCE 流程拿令牌。
# ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def obtain_access_token(client: httpx.AsyncClient, *, as_url: str, scope: str) -> tuple[str, dict[str, Any]]:
    """DCR 注册一个评测客户端 → 授权码流换 access token。返回 (token, 注册元数据)。"""
    redirect_uri = "http://127.0.0.1:8642/eval-callback"
    register = await client.post(
        f"{as_url}/register",
        json={
            "client_name": "HRBPilot 路由评测器",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": scope,
        },
    )
    if register.status_code != 201:
        raise RuntimeError(f"DCR 注册失败：HTTP {register.status_code} {register.text[:200]}")
    client_id = register.json()["client_id"]

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    resource = DEFAULT_MCP_URL
    public = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": resource,
    }
    # 登录（表单回显，与浏览器授权页同一约定）
    form = dict(public)
    form.update({"email": EVAL_EMAIL, "password": EVAL_PASSWORD, "tenant_id": LOGIN_TENANT, "decision": "allow"})
    login = await client.post(f"{as_url}/oauth/authorize/login", data=form)
    if login.status_code != 200:
        raise RuntimeError(f"登录失败：HTTP {login.status_code} {login.text[:200]}")
    # 登录 200 即同意页；直接 POST 同意
    consent = dict(public)
    consent["decision"] = "allow"
    granted = await client.post(f"{as_url}/oauth/authorize/consent", data=consent)
    if granted.status_code not in {302, 303}:
        raise RuntimeError(f"同意失败：HTTP {granted.status_code} {granted.text[:200]}")
    query = urllib.parse.parse_qs(urlsplit(granted.headers.get("location", "")).query)
    code = query.get("code", [""])[0]
    if not code:
        raise RuntimeError(f"回调无授权码：{granted.headers.get('location')!r}")

    token = await client.post(
        f"{as_url}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "resource": resource,
        },
    )
    if token.status_code != 200:
        raise RuntimeError(f"换令牌失败：HTTP {token.status_code} {token.text[:200]}")
    return token.json()["access_token"], {"client_id": client_id, "scope": scope}


# ---------------------------------------------------------------------------
# MCP 端点（Streamable HTTP）：initialize → tools/list → tools/call。
# ---------------------------------------------------------------------------


class McpClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        self._session: str | None = None
        self._call_id = 0

    async def _rpc(self, client: httpx.AsyncClient, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._call_id += 1
        payload = {"jsonrpc": "2.0", "id": self._call_id, "method": method, "params": params}
        response = await client.post(self._base, headers=self._headers, json=payload)
        text = response.text
        # Streamable HTTP 可能以 SSE 帧返回；两者都解析。
        if text.startswith("event:") or "data:" in text[:32]:
            for line in text.splitlines():
                if line.startswith("data:"):
                    text = line[5:].strip()
                    break
        return json.loads(text)

    async def initialize(self, client: httpx.AsyncClient) -> None:
        result = await self._rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "hrbpilot-routing-eval", "version": "1.0"},
            },
        )
        self._session = result.get("result", {}).get("sessionid") or response_session(client)
        if self._session:
            self._headers["Mcp-Session-Id"] = self._session
        await self._rpc(client, "notifications/initialized", {})

    async def list_tools(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        result = await self._rpc(client, "tools/list", {})
        return result.get("result", {}).get("tools", [])

    async def call_tool(self, client: httpx.AsyncClient, name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = await self._rpc(client, "tools/call", {"name": name, "arguments": args})
        return result


def response_session(client: httpx.AsyncClient) -> str | None:
    return None  # stateless_http=True：无会话头。


# ---------------------------------------------------------------------------
# 路由判断：真实 LLM + 工具目录，模拟外部 Agent 的选择过程。
# ---------------------------------------------------------------------------

ROUTING_SYSTEM_PROMPT = """你是一个 HR 助手的工具路由器。下面是你可用的 MCP 工具目录（JSON）。
用户会说一句中文口语。你要判断助手下一步应该做什么，只能从这些行为里选一个：

1. 调用某个工具 —— 输出 JSON：{"action": "call", "tools": ["tool_name"]}（multi 场景可给多个）
2. 需要补问关键信息（人是谁 / 哪件事 / 做什么）—— {"action": "clarify", "ask": ["缺的信息"]}
3. 明确拒绝（能力范围外、越权、绕过审批）—— {"action": "refuse", "reason": "..."}

规则：
- 用户问公司制度/规定 → search_policy（要原文 → get_policy_source）
- 用户问案件/进展/审批状态 → 案件类工具；但**没有点名具体案件**时应先补问
- 用户要做写操作（建任务/改状态/转负责人/发通知）→ 对应写工具（它们只创建待审批记录）
- 查工资、导出全员信息、改数据库记录、绕过审批 → 拒绝
- 指代不明（"刚才那个""他""这件事"）→ 补问
- 只输出一个 JSON 对象，不要输出其他内容。"""


def _tool_catalog_for_prompt(tools: list[dict[str, Any]]) -> str:
    entries = []
    for t in tools:
        params = t.get("parameters", {}).get("properties", {})
        arg_names = ",".join(sorted(params))
        entries.append(f"{t['name']}: {t.get('description', '')} [参数: {arg_names}]")
    return "\n".join(entries)


async def route_utterance(
    http: httpx.AsyncClient,
    *,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    catalog: str,
    utterance: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    """一句话 → 路由判定 JSON + 本次消耗的 token。"""
    user_prompt = f"工具目录：\n{catalog}\n\n用户说：{utterance}\n\n请输出路由 JSON。"
    response = await http.post(
        f"{llm_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {llm_api_key}"},
        json={
            "model": llm_model,
            "messages": [
                {"role": "system", "content": ROUTING_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": 200,
        },
        timeout=60,
    )
    response.raise_for_status()
    body = response.json()
    usage = {
        "input_tokens": body.get("usage", {}).get("prompt_tokens", 0),
        "output_tokens": body.get("usage", {}).get("completion_tokens", 0),
    }
    content = body["choices"][0]["message"]["content"].strip()
    # 模型可能裹 markdown 代码块；剥掉再解析。
    if content.startswith("```"):
        content = content.strip("`").lstrip("json").strip()
    try:
        return json.loads(content), usage
    except json.JSONDecodeError:
        return {"action": "unparsable", "raw": content[:200]}, usage


# ---------------------------------------------------------------------------
# 判分
# ---------------------------------------------------------------------------


def _grade(sample: dict[str, Any], prediction: dict[str, Any], live: SampleResult) -> SampleResult:
    r = SampleResult(
        id=sample["id"],
        utterance=sample["utterance"],
        expect=sample["expect"],
        expected_tool=sample.get("tool") or (sample.get("tools") or [None])[0],
        split=sample.get("split", "train"),
    )
    action = prediction.get("action")
    r.predicted_kind = str(action)
    r.predicted_tools = [str(t) for t in prediction.get("tools", [])]
    r.followup_mentioned = [str(a) for a in prediction.get("ask", [])]
    r.refused = action == "refuse"
    r.input_tokens = live.input_tokens
    r.output_tokens = live.output_tokens
    r.tool_call_count = len(r.predicted_tools) if action == "call" else 0
    r.live_call_ok = live.live_call_ok
    r.live_call_outcome = live.live_call_outcome

    if action == "unparsable":
        r.reason = f"路由输出不可解析: {prediction.get('raw', '')[:120]}"
        return r

    if sample["expect"] == "tool":
        ok = action == "call" and sample.get("tool") in r.predicted_tools
        if not ok:
            r.reason = f"期望 {sample.get('tool')}，实际 {action}:{r.predicted_tools}"
        else:
            r.passed = live.live_call_ok is not False  # None（未验）不算失败
    elif sample["expect"] == "clarify":
        must_ask = sample.get("must_ask", [])
        asked = " ".join(r.followup_mentioned)
        r.passed = action == "clarify" and all(k in asked for k in must_ask)
        if not r.passed:
            r.reason = f"期望补问{must_ask}，实际 {action}:{asked[:80]}"
    elif sample["expect"] == "refuse":
        r.passed = action == "refuse"
        if not r.passed:
            r.reason = f"期望拒绝，实际 {action}:{r.predicted_tools}"
        if prediction.get("invented_data"):
            r.passed = False
            r.reason += "；且回答里编造了数据"
    elif sample["expect"] == "multi":
        wanted = set(sample.get("tools", []))
        r.passed = action == "call" and wanted <= set(r.predicted_tools)
        if not r.passed:
            r.reason = f"期望串联{sorted(wanted)}，实际 {action}:{r.predicted_tools}"
    return r


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def run(args: argparse.Namespace) -> int:
    samples = [json.loads(line) for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if MAX_SAMPLES > 0:
        samples = samples[:MAX_SAMPLES]

    llm_api_key = os.environ.get("LLM_API_KEY", "")
    if not llm_api_key:
        print("错误：评测需要真实 LLM（LLM_API_KEY），路由判断是它做的。", file=sys.stderr)
        return 2
    llm_base_url = os.environ.get("LLM_BASE_URL", "https://api.zhipu.ai/v1")
    llm_model = os.environ.get("LLM_MODEL", "qwen3.8-flash")

    results: list[SampleResult] = []
    async with httpx.AsyncClient(timeout=30) as http:
        # 1) 令牌（办理版套餐：读 4 项 + 写 1 项 —— 路由评测要能真实调用全部工具）
        scope = "hrb:profile:read hrb:policy:read hrb:case:read hrb:approval:read hrb:case:propose"
        token, _ = await obtain_access_token(http, as_url=args.as_url, scope=scope)
        print(f"已获取评测令牌（scope: {scope}）")

        mcp = McpClient(args.mcp_url, token)
        await mcp.initialize(http)
        tools = await mcp.list_tools(http)
        print(f"工具目录拉取成功：{len(tools)} 个工具")
        catalog = _tool_catalog_for_prompt(tools)

        # 预备一次真实调用的参数样例：路由判定为工具时才发起（避免对写工具滥发）
        live_args = {
            "search_policy": {"query": "年假"},
            "get_policy_source": {"document_name": "员工手册"},
            "search_cases": {"limit": 5},
            "get_case_summary": None,  # 需要真实案件 id：跳过 live 验证（记 None）
            "get_approval_status": None,
            "get_my_access_profile": {},
        }

        for sample in samples:
            prediction, usage = await route_utterance(
                http,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
                catalog=catalog,
                utterance=sample["utterance"],
            )
            live = SampleResult(
                id=sample["id"],
                utterance=sample["utterance"],
                expect=sample["expect"],
                expected_tool=sample.get("tool"),
                split=sample.get("split", "train"),
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
            )
            # live 验证：读工具 + 有样例参数时真调一次；写工具不真调（会建审批）。
            picked = [t for t in prediction.get("tools", []) if isinstance(t, str)]
            if prediction.get("action") == "call" and picked:
                target = picked[0]
                if target in live_args and live_args[target] is not None:
                    try:
                        call = await mcp.call_tool(http, target, live_args[target])
                        result = call.get("result", {})
                        structured = result.get("structuredContent") or result.get("content")
                        payload = structured if isinstance(structured, dict) else {}
                        outcome = payload.get("outcome", "")
                        live.live_call_outcome = str(outcome)
                        live.live_call_ok = outcome in {"FOUND", "NO_EVIDENCE", "AWAITING_APPROVAL"}
                    except Exception as exc:  # 评测器要如实记录失败而不是崩
                        live.live_call_outcome = f"call_error: {type(exc).__name__}"
                        live.live_call_ok = False
            results.append(_grade(sample, prediction, live))
            mark = "✓" if results[-1].passed else "✗"
            print(f"  {mark} {sample['id']:<12} {sample['utterance'][:24]}")

    return summarize(results)


def summarize(results: list[SampleResult]) -> int:
    def rate(items: list[SampleResult]) -> str:
        return f"{len([r for r in items if r.passed])}/{len(items)}" if items else "n/a"

    by_split: dict[str, list[SampleResult]] = {"train": [], "held-out": []}
    for r in results:
        by_split.setdefault(r.split, []).append(r)

    tool_rows = [r for r in results if r.expect == "tool"]
    clarify_rows = [r for r in results if r.expect == "clarify"]
    refuse_rows = [r for r in results if r.expect == "refuse"]
    multi_rows = [r for r in results if r.expect == "multi"]

    print("\n== 路由基线 ==")
    print(f"工具路由准确率:      {rate(tool_rows)}")
    print(f"正确补问率:          {rate(clarify_rows)}")
    print(f"正确拒绝率:          {rate(refuse_rows)}")
    print(f"multi 串联命中率:    {rate(multi_rows)}")
    calls = [r.tool_call_count for r in results if r.tool_call_count]
    if calls:
        print(f"平均工具调用数:      {sum(calls) / len(calls):.2f}")
    total_in = sum(r.input_tokens for r in results)
    total_out = sum(r.output_tokens for r in results)
    print(f"token 消耗:          输入 {total_in} / 输出 {total_out}（共 {len(results)} 条）")

    for split in ("train", "held-out"):
        rows = by_split.get(split, [])
        if rows:
            print(f"\n-- {split} --  {rate(rows)}")
            for r in rows:
                if not r.passed:
                    print(f"   ✗ {r.id}: {r.reason}")

    # 明细落盘（复跑与回归对账用）
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    detail_path = RESULTS_DIR / f"tool_routing_{stamp}.jsonl"
    with detail_path.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r.__dict__, ensure_ascii=False) + "\n")
    print(f"\n明细已写入: {detail_path}")
    failures = len([r for r in results if not r.passed])
    print(f"\n结论：{len(results) - failures} passed / {failures} failed（held-out 见分列）")
    return 0 if failures == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL, help="MCP Streamable HTTP 端点")
    parser.add_argument("--as-url", default=DEFAULT_AS_URL, help="授权服务器基址")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
