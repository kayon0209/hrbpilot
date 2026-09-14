"""AS 的 HTML 渲染（登录页与同意页）。

用 Jinja2 而不是拼字符串：这两页会把**用户可控的数据**（客户端名、scope 列表、
错误描述、以及回显的授权参数）写进 HTML。Jinja2 的自动转义在这里不是锦上添花 ——
客户端名来自 CIMD 文档，是**完全由外部提供**的字符串，直接拼进页面等于把授权页
变成一个存储型 XSS 的入口。

模板文件与代码放在一起（``templates/``），不引入额外的静态资源目录：这两页不需要
任何 JS，授权页越像"一个静态表单"越好。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_environment = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

#: 任何会写进页面的响应都带这两个头。AS 的页面不需要脚本与框架，
#: 于是可以直接把它们全部关掉 —— 万一将来有人注入了一个 <script>，
#: CSP 让它什么都做不了。这是纵深防御，不是替代转义。
_HARDENED_HEADERS = {
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}

_CSP_FORM_ACTION_PREFIX = "default-src 'none'; style-src 'unsafe-inline'; form-action "
_CSP_SUFFIX = "; base-uri 'none'"

#: 允许出现在 ``form-action`` 里的源的形态：``scheme://authority``（http/https 回调）
#: 或裸的 ``scheme:``（自定义协议回调，如 ``workbuddy:``）。
#: 要挡的是"能把别的指令注入进来"的字符（空格、分号、引号、逗号）—— 这些在 URI 里
#: 本来也不合法，直接丢弃这一项，宁可让跳转被拦，也不要放出一个被拼接过的 CSP。
_FORM_ACTION_SOURCE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:(//[A-Za-z0-9.\-\[\]:]+)?$")


def _form_action_header(origins: Iterable[str]) -> str:
    """拼 ``form-action``：默认只有 ``'self'``，外加调用方显式给出的回调源。

    ``'self'`` 单独一条是不够的：授权页的表单 POST 之后服务端会 302 回客户端回调，
    而 **Chrome 与 Safari 会把 ``form-action`` 也施加到那次重定向**（Firefox 不会）。
    客户端回调几乎总是**别的源**（loopback 随机端口、自定义协议、平台域名），于是
    "点授权后页面毫无反应、不跳转"—— 服务端其实已经签发了授权码。所以这里必须把
    **那一个**已经过校验的回调源显式列出，且只列这一个：不做通配、不做全局放宽。
    """
    sources = ["'self'"]
    for origin in origins:
        candidate = (origin or "").strip()
        if candidate and candidate not in sources and _FORM_ACTION_SOURCE.match(candidate):
            sources.append(candidate)
    return _CSP_FORM_ACTION_PREFIX + " ".join(sources) + _CSP_SUFFIX


def render_page(
    template_name: str,
    *,
    status_code: int = 200,
    form_action_origins: Iterable[str] = (),
    **context: Any,
) -> HTMLResponse:
    headers = {**_HARDENED_HEADERS, "Content-Security-Policy": _form_action_header(form_action_origins)}
    html = _environment.get_template(template_name).render(**context)
    return HTMLResponse(html, status_code=status_code, headers=headers)
