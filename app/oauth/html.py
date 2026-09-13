"""AS 的 HTML 渲染（登录页与同意页）。

用 Jinja2 而不是拼字符串：这两页会把**用户可控的数据**（客户端名、scope 列表、
错误描述、以及回显的授权参数）写进 HTML。Jinja2 的自动转义在这里不是锦上添花 ——
客户端名来自 CIMD 文档，是**完全由外部提供**的字符串，直接拼进页面等于把授权页
变成一个存储型 XSS 的入口。

模板文件与代码放在一起（``templates/``），不引入额外的静态资源目录：这两页不需要
任何 JS，授权页越像"一个静态表单"越好。
"""

from __future__ import annotations

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
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def render_page(template_name: str, *, status_code: int = 200, **context: Any) -> HTMLResponse:
    html = _environment.get_template(template_name).render(**context)
    return HTMLResponse(html, status_code=status_code, headers=_HARDENED_HEADERS)
