"""任务类型注册表与意图编译器 —— ``prepare_hr_action`` 的服务端解释器。

为什么是规则而不是模型
----------------------
外部 Agent（Codex/WorkBuddy）负责理解自然语言并选工具；到了 ``prepare_hr_action``
这一层，输入已经是"一句目标 + 可选案件引用"。服务端要的是**确定、可回归、可审计**
的规范化：同一句话在任何进程、任何时刻编译出同一份冻结草稿。LLM 在这里只会引入
不可复现的草稿漂移 —— 而草稿一旦漂移，用户确认的就不是系统将要执行的东西。

任务类型是稳定的对外契约（task_type），不是底层函数名：底层审批工具怎么换，
外部客户端看到的类型不变。

编译器的诚实原则
----------------
- 解析不出来的字段进 ``missing_fields`` 按固定顺序问，绝不猜一个值填上；
- 相对日期只认覆盖内的高置信写法（下周X/明天/ISO 日期），歧义写法宁可问；
- 负责人按姓名在本租户用户目录里找唯一匹配，找不到或不唯一就回落到
  「发起人自己 + waiting_for 记原名」，不伪造 user_id。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

#: 草稿缺字段时的**固定**追问顺序（capability manifest 的 ask_order 与此同源）。
FIELD_ASK_ORDER = ("case_ref", "title", "next_action", "owner", "due_at", "recipient_ref", "template")


@dataclass(frozen=True, slots=True)
class FieldSpec:
    key: str
    label: str
    question: str
    required: bool = True
    #: text | date | user_name
    kind: str = "text"


@dataclass(frozen=True, slots=True)
class TaskTypeDef:
    task_type: str
    title: str
    description: str
    keywords: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    #: 提交时走的**现有**审批工具（app/scenarios/hr_case_agent/tools.py 白名单内）。
    approval_tool: str
    requires_case: bool = True
    risk_level: str = "medium"
    risk_reasons: tuple[str, ...] = ("将创建业务记录并进入人工审批",)
    #: 计划步骤的人类可读模板；{n} 处由编译结果填充。
    planned_steps: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _steps(*pairs: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(pairs)


TASK_TYPES: dict[str, TaskTypeDef] = {
    "case_followup": TaskTypeDef(
        task_type="case_followup",
        title="案件跟进待办",
        description="为一个人事案件创建跟进任务，指定下一步、负责人与截止时间。",
        keywords=("跟进", "待办", "补材料", "补齐", "处理一下", "任务"),
        fields=(
            FieldSpec("case_ref", "案件", "这个跟进挂在哪一个案件上？（可先用案件查询拿到编号）", True, "text"),
            FieldSpec("title", "任务标题", "这条跟进任务叫什么名字？", True, "text"),
            FieldSpec("next_action", "下一步动作", "下一步具体要做什么？", False, "text"),
            FieldSpec("owner", "负责人", "由谁负责跟进？", False, "user_name"),
            FieldSpec("due_at", "截止时间", "什么时候前完成？（如 2026-09-23 或下周三）", False, "date"),
        ),
        approval_tool="create_work_task",
        planned_steps=_steps(
            ("创建跟进任务并指定负责人", "待审批"),
            ("审批通过后任务出现在任务中心与负责人的待办里", "审批后执行"),
        ),
    ),
    "probation_followup": TaskTypeDef(
        task_type="probation_followup",
        title="试用期异常跟进",
        description="围绕试用期异常（材料缺失、考核风险、转正评估）为员工建立案件跟进任务。",
        keywords=("试用期", "转正", "试用异常", "试用期异常"),
        fields=(
            FieldSpec("case_ref", "案件", "这条试用期跟进对应哪个案件？（可先用案件查询找到编号）", True, "text"),
            FieldSpec("title", "任务标题", "跟进任务的标题叫什么？", True, "text"),
            FieldSpec("next_action", "下一步动作", "下一步要做什么（联系员工、补材料、安排评估）？", False, "text"),
            FieldSpec("owner", "负责人", "由谁负责跟进？", False, "user_name"),
            FieldSpec("due_at", "截止时间", "什么时候前完成？", False, "date"),
        ),
        approval_tool="create_work_task",
        planned_steps=_steps(
            ("在指定案件上创建试用期异常跟进任务", "待审批"),
            ("审批通过后任务生效并通知负责人", "审批后执行"),
        ),
    ),
    "case_owner_change": TaskTypeDef(
        task_type="case_owner_change",
        title="变更案件负责人",
        description="把一个案件转给指定负责人跟进。",
        keywords=("转给", "交给", "换人", "负责人", "指派"),
        fields=(
            FieldSpec("case_ref", "案件", "要转出的案件是哪一个？", True, "text"),
            FieldSpec("owner", "新负责人", "转给谁负责？（请填写系统内成员的姓名）", True, "user_name"),
        ),
        approval_tool="assign_case_owner",
        planned_steps=_steps(("变更该案件的负责人", "待审批"), ("审批通过后案件负责人生效", "审批后执行")),
    ),
    "case_resolve": TaskTypeDef(
        task_type="case_resolve",
        title="结案（标记已解决）",
        description="把案件标记为已解决。",
        keywords=("结案", "已解决", "解决掉", "关闭案件", "处理完了"),
        fields=(FieldSpec("case_ref", "案件", "要结案的是哪一个案件？", True, "text"),),
        approval_tool="update_case_status",
        planned_steps=_steps(("将该案件标记为已解决", "待审批"), ("审批通过后案件状态生效", "审批后执行")),
    ),
    "case_notification": TaskTypeDef(
        task_type="case_notification",
        title="案件站内通知",
        description="就某个案件向相关同事发送一条站内通知。",
        keywords=("通知", "提醒", "知会", "告诉"),
        fields=(
            FieldSpec("case_ref", "案件", "通知针对哪个案件？", True, "text"),
            FieldSpec("recipient_ref", "接收人", "通知发给谁？（请填写系统内成员的姓名）", True, "user_name"),
            FieldSpec("template", "通知内容", "通知要说什么？（一句话）", True, "text"),
        ),
        approval_tool="send_case_notification",
        # 通知是外发副作用：风险抬到 high，确认卡必须写清接收人与内容。
        risk_level="high",
        risk_reasons=("会向他人发出通知", "内容经审批人确认后才会送达"),
        planned_steps=_steps(("发送这条站内通知", "待审批"), ("审批通过后接收人收到通知", "审批后执行")),
    ),
}

_CASE_TOKEN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_ISO_DATE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?")
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_REL_WEEKDAY = re.compile(r"(下|这|本)周([一二三四五六日天])")
_OWNER = re.compile(r"(?:由|交给|让)([\u4e00-\u9fa5A-Za-z]{1,10}?)(?:负责|跟进|处理|完成|来|牵头|去办)")
_RECIPIENT = re.compile(r"(?:通知|提醒|知会|告诉)([\u4e00-\u9fa5A-Za-z]{1,10}?)(?:一声|一下|他|她|同事|@|,|，|$)")
_TITLE_STRIP = re.compile(
    r"[，。;；]?\s*(?:下周三前|本周[一二三四五六日天]前|明天前|后天前|由[\u4e00-\u9fa5]{1,10}?负责|尽快|今天|下周)"
)
_TITLE_REJECT = re.compile(r"(?:给|为|帮|把)?(?:一个|某|这条|那个|该)案件|建一个|创建一个|处理一下|跟进任务|建跟进")


def _extract_title(text: str) -> str:
    """从目标句提取可作标题的短语；泛化说法（“给一个案件建跟进任务”）返回空串
    —— 宁可在 missing_fields 里问一句，也不把整句或泛词冻结成标题。"""
    title = _TITLE_STRIP.sub("", text).strip("，。;； ")
    if len(title) < 4 or _TITLE_REJECT.search(title):
        return ""
    return title


def detect_task_type(goal: str) -> str | None:
    """关键词打分选类型；多类型同分时偏向**更具体**。

    具体性按“命中的最长关键词”判定：通用词（跟进/任务）短，领域词（试用期/
    结案/转给）长。得分键为（最长命中长度，命中总数），再按注册顺序兜底，
    保证确定、可回归。"""

    best_type: str | None = None
    best_key: tuple[int, int] | None = None
    for spec in TASK_TYPES.values():
        hits = [kw for kw in spec.keywords if kw in goal]
        if not hits:
            continue
        key = (max(len(kw) for kw in hits), len(hits))
        if best_key is None or key > best_key:
            best_type, best_key = spec.task_type, key
    return best_type


def parse_relative_date(text: str, now: datetime) -> str | None:
    """把高置信的中文相对日期解析成 ISO 8601 字符串；解析不了返回 ``None``。

    时区约定：返回**UTC 当日 09:00** 的 ISO 串 —— 只给到"哪天"时，用一个明确的
    业务时刻（工作日上午），不伪造"用户没说过的 23:59"。
    """
    iso = _ISO_DATE.search(text)
    if iso:
        year, month, day = (int(iso.group(i)) for i in (1, 2, 3))
        try:
            return datetime(year, month, day, 9, 0, tzinfo=UTC).isoformat()
        except ValueError:
            return None
    if "明天" in text:
        target = now + timedelta(days=1)
        return target.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    if "后天" in text:
        target = now + timedelta(days=2)
        return target.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    rel = _REL_WEEKDAY.search(text)
    if rel:
        prefix, day_char = rel.group(1), rel.group(2)
        target_weekday = _WEEKDAY[day_char]
        if prefix == "下":
            days_to_next_monday = 7 - now.weekday()
            target = now + timedelta(days=days_to_next_monday + target_weekday)
        else:
            days_ahead = (target_weekday - now.weekday()) % 7
            target = now + timedelta(days=days_ahead)
        return target.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    return None


@dataclass(slots=True)
class CompileResult:
    task_type: str | None
    params: dict[str, str] = field(default_factory=dict)
    missing: list[dict[str, str]] = field(default_factory=list)
    interpreted_goal: str = ""
    risk_level: str = "medium"
    risk_reasons: list[str] = field(default_factory=list)


def _missing_payload(spec: FieldSpec) -> dict[str, str]:
    return {"field": spec.key, "label": spec.label, "question": spec.question}


def compile_goal(goal: str, *, case_ref: str | None = None, now: datetime | None = None) -> CompileResult:
    """自然语言目标 → 规范化参数 + 缺失字段清单（纯函数，可回归）。"""
    now = now or datetime.now(UTC)
    text = (goal or "").strip()
    task_type = detect_task_type(text)
    if task_type is None:
        return CompileResult(task_type=None)
    spec = TASK_TYPES[task_type]
    params: dict[str, str] = {}

    case_hit = _CASE_TOKEN.search(text)
    resolved_case = (case_ref or "").strip() or (case_hit.group(0) if case_hit else "")
    if resolved_case:
        params["case_ref"] = resolved_case

    due = parse_relative_date(text, now)
    if due:
        params["due_at"] = due

    owner = _OWNER.search(text)
    if owner:
        params["owner"] = owner.group(1)
    if spec.task_type == "case_owner_change" and not owner:
        alt = re.search(r"(?:转给|交给|指派给)([\u4e00-\u9fa5A-Za-z]{1,10}?)(?:负责|跟进|处理|[，。;；]|$)", text)
        if alt:
            params["owner"] = alt.group(1)

    recipient = _RECIPIENT.search(text)
    if recipient:
        params["recipient_ref"] = recipient.group(1)

    title = _extract_title(text)
    if title:
        params["title"] = title[:200]
    if spec.task_type in ("probation_followup",) and "title" not in params:
        params["title"] = spec.title
    if spec.task_type == "case_notification":
        params.setdefault("template", "followup")

    missing = [
        _missing_payload(field_spec)
        for field_spec in sorted(spec.fields, key=lambda f: FIELD_ASK_ORDER.index(f.key))
        if field_spec.required and not params.get(field_spec.key)
    ]
    return CompileResult(
        task_type=task_type,
        params=params,
        missing=missing,
        interpreted_goal=_interpret(task_type, params),
        risk_level=spec.risk_level,
        risk_reasons=list(spec.risk_reasons),
    )


def _interpret(task_type: str, params: dict[str, str]) -> str:
    spec = TASK_TYPES[task_type]
    parts = [spec.title]
    if params.get("title"):
        parts.append(f"内容：{params['title']}")
    if params.get("owner"):
        parts.append(f"负责人：{params['owner']}")
    if params.get("due_at"):
        parts.append(f"截止：{params['due_at'][:10]}")
    return "；".join(parts)[:500]


def normalized_params(task_type: str, raw: dict[str, str]) -> dict[str, str]:
    """冻结前的规范化：只保留该类型认识的键、去除两端空白、按键排序。

    hash 建立在**这个函数**的输出上 —— 键顺序、空白、多余键都不应造成两份
    内容相同的草稿有不同 hash。
    """
    spec = TASK_TYPES[task_type]
    known = {f.key for f in spec.fields}
    cleaned = {k: v.strip() for k, v in raw.items() if k in known and isinstance(v, str) and v.strip()}
    return dict(sorted(cleaned.items()))
