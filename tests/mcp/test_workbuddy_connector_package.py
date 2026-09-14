"""WorkBuddy 连接器包的结构与一致性。

为什么值得为几个 JSON 文件写测试
--------------------------------
连接器包是**提交给第三方审核并进入市场**的东西。它的问题不会让 CI 变红，也不会让
任何测试失败 —— 它会让用户在客户端上"连不上"，或者让 AI 去调用一个名字写错的工具，
而错误现场在用户机器上。这类缺陷靠 review 是抓不住的（一个拼错的工具名看起来完全
正常），只能靠把"名字必须存在"变成断言。

同时守一条硬规矩：包里不得出现真实凭证。示例配置最容易被抄进真实部署，一个写死的
令牌会沿着"复制粘贴"扩散。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.scenarios.hr_case_agent.tools import TOOL_CATALOG

_PACKAGE = Path(__file__).resolve().parents[2] / "connectors" / "workbuddy"
_TOOL_NAMES = {tool.name for tool in TOOL_CATALOG.tools}


def _load(name: str) -> dict:
    return json.loads((_PACKAGE / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def meta() -> dict:
    return _load("connector-meta.json")


@pytest.fixture(scope="module")
def mcp() -> dict:
    return _load("mcp.json")


# --------------------------------------------------------------------------- #
# 结构
# --------------------------------------------------------------------------- #


def test_the_required_files_exist() -> None:
    for name in ("connector-meta.json", "mcp.json", "icon.svg"):
        assert (_PACKAGE / name).is_file(), f"缺少必需文件 {name}"
    assert (_PACKAGE / "skills" / "hrbpilot" / "SKILL.md").is_file()


def test_the_source_is_kebab_case(meta: dict) -> None:
    """规范要求 source 为 kebab-case 且全局唯一。"""
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", meta["source"]), meta["source"]


def test_the_connector_is_an_mcp_connector(meta: dict) -> None:
    assert meta["type"] == "mcp"


def test_examples_are_present_in_both_languages(meta: dict) -> None:
    """示例是市场里最影响用户判断的部分；各 2—5 条是规范建议。"""
    for key in ("examples_zh", "examples_en"):
        assert 2 <= len(meta[key]) <= 5, f"{key} 条数不合规"


def test_newer_fields_declare_a_minimum_version(meta: dict) -> None:
    """用了 ``examples_zh`` 就必须声明 ``minWorkbuddyVersion``（该字段 4.24.0 起生效）。

    不声明的话，低版本客户端会拉到一份它读不懂的配置 —— 而失败表现是"市场里这个
    连接器点了没反应"。
    """
    assert "minWorkbuddyVersion" in meta
    assert tuple(int(part) for part in meta["minWorkbuddyVersion"].split(".")) >= (4, 24, 0)


# --------------------------------------------------------------------------- #
# mcp.json
# --------------------------------------------------------------------------- #


def test_exactly_one_server_is_configured(mcp: dict) -> None:
    """规范：一个连接器只配置一个 MCP Server。"""
    assert len(mcp["mcpServers"]) == 1


def test_the_remote_url_uses_https(mcp: dict) -> None:
    server = next(iter(mcp["mcpServers"].values()))
    assert server["type"] == "streamableHttp"
    assert server["url"].startswith("https://"), "远程 MCP 必须使用 HTTPS"


def test_disabled_tools_actually_exist(mcp: dict) -> None:
    """关错名字等于没关：`disabledTools` 里的拼写错误不会报错，只会静默失效。"""
    server = next(iter(mcp["mcpServers"].values()))
    unknown = set(server.get("disabledTools", [])) - _TOOL_NAMES
    assert not unknown, f"disabledTools 里有不存在的工具：{sorted(unknown)}"


def test_the_connector_does_not_have_to_hide_anything(mcp: dict) -> None:
    """连接器不需要 ``disabledTools``。

    它此前用来挡掉 ``hrbpilot_ping`` —— 那个健康探针已从 MCP 出口移除，因为它是
    开发者视角的调试工具，而且不在工具目录里、完全绕过了授权判定。把开发者工具从
    源头拿掉，比在每个客户端配置里逐个关掉更彻底。
    """
    server = next(iter(mcp["mcpServers"].values()))
    assert "disabledTools" not in server


# --------------------------------------------------------------------------- #
# SKILL.md
# --------------------------------------------------------------------------- #


def _skill_text() -> str:
    return (_PACKAGE / "skills" / "hrbpilot" / "SKILL.md").read_text(encoding="utf-8")


def test_the_skill_declares_frontmatter() -> None:
    text = _skill_text()
    assert text.startswith("---\n")
    assert "\nname: hrbpilot\n" in text
    assert "\ndescription:" in text


def test_every_tool_named_in_the_skill_exists() -> None:
    """SKILL.md 里提到的工具名必须在真实目录里存在。

    这是本文件存在的主要理由：一个拼错的工具名在 review 时看起来完全正常，而 AI
    会照着它去调用一个不存在的能力。
    """
    mentioned = set(re.findall(r"`([a-z][a-z0-9_]{3,})`", _skill_text()))
    # 只校验"看起来像工具名"的：含下划线且不在已知非工具词里。
    non_tools = {"case_id", "no_evidence", "awaiting_approval", "auth_required", "input_schema"}
    candidates = {name for name in mentioned if "_" in name and name.lower() not in non_tools and not name.isupper()}
    unknown = candidates - _TOOL_NAMES
    assert not unknown, f"SKILL.md 提到了不存在的工具：{sorted(unknown)}"


def test_the_skill_says_approval_is_not_execution() -> None:
    """最要紧的一条面向用户的语义：提交审批 ≠ 已完成。

    它不能只写在代码注释里 —— AI 的行为由 Skill 决定，如果 Skill 不要求它这么说，
    用户就会看到"已完成"，而实际上什么都没发生。
    """
    text = _skill_text()
    assert "已提交审批" in text
    assert "已完成" in text  # 明确禁止使用
    assert "不要" in text


def test_the_skill_forbids_inventing_policy_answers() -> None:
    text = _skill_text()
    assert "没找到" in text or "没有检索到" in text


# --------------------------------------------------------------------------- #
# 凭证
# --------------------------------------------------------------------------- #


def test_no_credential_looking_value_is_committed() -> None:
    """示例配置最容易被抄进真实部署，一个写死的令牌会沿着复制粘贴扩散。"""
    suspicious = re.compile(r"(eyJ[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{16,}|Bearer [A-Za-z0-9._-]{20,})")
    for path in _PACKAGE.rglob("*"):
        if not path.is_file() or path.suffix == ".pyc":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not suspicious.search(text), f"{path.name} 里出现了疑似真实凭据"


def test_the_placeholder_domain_is_documented() -> None:
    """占位域名必须在 README 里被点明，否则会连同包一起提交上去。"""
    readme = (_PACKAGE / "README.md").read_text(encoding="utf-8")
    assert "example.com" in readme
    assert "必须替换" in readme or "必须" in readme
