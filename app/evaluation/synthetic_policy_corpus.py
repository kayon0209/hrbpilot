"""Synthetic deterministic policy corpus for Policy QA citation evaluation.

Every golden `expected_citations` document name maps to a real, chunkable
policy text whose sections contain the facts the golden questions ask about.
Content is SYNTHETIC (demo regulations, no real company policy) and the
retrieval harness built on it is fully deterministic — no LLM, no network,
no embedding. Runs using this corpus are labeled OFFLINE-DETERMINISTIC.

Structure rule: each document text repeats the question's key nouns so the
production sparse tokenizer (jieba → PG tsquery) finds the right chunk, and
sections follow the "第X章/第X条" heading pattern the section chunker splits on.
"""

from dataclasses import dataclass, field


@dataclass
class SyntheticDoc:
    """One synthetic policy document: title, filename, and sectioned text."""

    title: str
    filename: str
    sections: list[tuple[str, str]] = field(default_factory=list)  # (heading, body)

    def full_text(self) -> str:
        return "\n\n".join(f"{heading}\n{body}" for heading, body in self.sections)


# NOTE: bodies deliberately use the same noun vocabulary as the golden
# `input`/`expected_output_contains` so sparse retrieval is exercised on
# its real terms (e.g. 年假/顺延/3月31日). Numbers mirror the golden labels.
_DOCS: list[SyntheticDoc] = [
    SyntheticDoc(
        title="员工手册",
        filename="员工手册.pdf",
        sections=[
            ("第一章 总则", "本手册是员工了解公司行为规范的总纲。"),
            (
                "第二章 年假",
                "年假没休完可以顺延到次年3月31日。",
            ),
            (
                "第三章 病假事假",
                "事假为无薪假，事假扣工资。",
            ),
            (
                "第四章 丧假与哺乳假",
                "丧假几天：丧假针对直系亲属，需提交申请。哺乳假每天一小时，产后一年内适用。",
            ),
            (
                "第五章 试用期与转正",
                "试用期长度按劳动合同期限核定，试用期最长不超过六个月。试用期内不能休年假，年假需满一年后享受。"
                "转正需要提交转正申请，经过评估与审批流程。试用期工资不低于转正工资的百分之八十。",
            ),
            (
                "第六章 工作时间与加班",
                "公司上班时间为上午九点，实行弹性工作制，可申请弹性安排。加班需审批，加班最晚时间受限制。"
                "忘打卡需在系统内提交补卡申请。外勤人员使用移动打卡。旷工指未请假擅自缺勤，将受到处罚。"
                "迟到累计次数会触发警告。",
            ),
            (
                "第七章 远程办公与保密",
                "员工可以申请远程办公或居家办公，需部门审批。保密协议签署后具有法律效力，员工负有保密义务。",
            ),
            (
                "第六章 培训与发展",
                "公司提供入职培训与在职技能培训。入职培训帮助新员工了解制度。培训内容与岗位实践结合。",
            ),
        ],
    ),
    SyntheticDoc(
        title="休假管理制度",
        filename="休假管理制度.pdf",
        sections=[
            ("第一章 总则", "本制度规范各类假期的申请流程、天数核定与审批权限。"),
            (
                "第二章 婚假与产假",
                "婚假有多少天：婚假天数为三天，符合晚婚条件的为七天。产假申请需提交医院证明与生育津贴材料，产假流程见审批细则。",
            ),
            (
                "第三章 年假与顺延",
                "年假天数按工龄核定。年假没休完可以顺延，顺延截止日为次年3月31日。年假申请需提前提交。",
            ),
            (
                "第四章 病假事假",
                "病假需要医院证明与诊断书。事假按天扣工资，属于无薪假。",
            ),
            (
                "第五章 丧假",
                "丧假几天：丧假针对直系亲属，丧假需提交申请，天数按员工手册执行。",
            ),
            (
                "第五章 哺乳假",
                "哺乳假每天一小时，适用于产后一年内的员工，需向HR备案。",
            ),
        ],
    ),
    SyntheticDoc(
        title="考勤管理制度",
        filename="考勤管理制度.pdf",
        sections=[
            ("第一章 总则", "本制度规范作息时间、打卡、迟到旷工与加班管理。"),
            (
                "第二章 作息与打卡",
                "上班时间为上午九点，公司实行弹性工作制。忘打卡需提交补卡申请并经审批。"
                "外勤人员通过移动端打卡，支持GPS定位。远程办公与居家办公需另行申请。",
            ),
            (
                "第三章 迟到与旷工",
                "迟到按考勤标准扣款，迟到次数累计会触发警告。旷工的定义：旷工指未请假擅自缺勤，旷工将按处罚条例处理。",
            ),
            (
                "第四章 加班与调休",
                "加班需事先审批，加班最晚时间有限制。加班可以按调休换休，调休补偿按折算规则执行。事假能请多少天按本制度核定，事假扣工资标准按天数计算。",
            ),
        ],
    ),
    SyntheticDoc(
        title="薪酬福利管理制度",
        filename="薪酬福利管理制度.pdf",
        sections=[
            ("第一章 总则", "本制度规范工资结构与社保公积金。"),
            (
                "第二章 工资与发薪",
                "工资什么时候发：发薪日期为每月10号，遇节假日提前。工资条载明税前税后明细与扣除项。"
                "试用期工资打八折（打几折：八折），即不低于转正工资的百分之八十，也不低于劳动合同约定。",
            ),
            (
                "第三章 加班费",
                "加班费按标准计算：工作日加班1.5倍，周末加班2倍，法定节假日加班3倍。加班费计算以审批记录为准。",
            ),
            (
                "第四章 社保与公积金",
                "社保缴纳比例按五险核定，包含养老与医疗保险。公积金基数按比例缴纳，每年调整一次。",
            ),
            (
                "第五章 年终奖与调薪",
                "年底有年终奖：年终奖怎么算与年度绩效考核结果挂钩发放。调薪与考核成绩挂钩，离职时未休年假按日薪折算补偿。",
            ),
            (
                "第六章 出差与报销",
                "出差补贴按标准执行，出差费用报销需提交票据并走审批流程。",
            ),
        ],
    ),
    SyntheticDoc(
        title="绩效考核管理办法",
        filename="绩效考核管理办法.pdf",
        sections=[
            ("第一章 总则", "本办法规范绩效考核周期、结果应用、申诉与改进。"),
            (
                "第二章 考核周期",
                "绩效考核按季度考核与年度考核相结合，考核周期由HR统一安排。",
            ),
            (
                "第三章 结果应用",
                "绩效C需要制定改进计划，进入PIP辅导流程。晋升需满足绩效、年限与能力条件。"
                "调薪与绩效挂钩。绩效结果与年终奖挂钩。",
            ),
            (
                "第四章 申诉",
                "员工对绩效结果有异议可发起申诉，申诉流程包含反馈与复核环节。",
            ),
        ],
    ),
    SyntheticDoc(
        title="劳动合同管理规定",
        filename="劳动合同管理规定.pdf",
        sections=[
            ("第一章 总则", "本规定规范劳动合同订立、试用期、续签与解除。"),
            (
                "第二章 试用期",
                "试用期期限按合同期核定：三个月以上不满一年的试用期一个月，一年以上三年以下试用期三个月，"
                "三年以上试用期六个月。KPI未完成不能直接开除，应先安排改进。",
            ),
            (
                "第三章 续签与补偿",
                "合同到期不续签的，按N+1标准支付经济补偿。签了三年合同想提前走可以提前离职，需提前30天书面通知，违约责任按约定。",
            ),
            (
                "第四章 竞业与保密",
                "公司对负有保密义务的员工提供竞业限制补偿。竞业限制期限与保密协议约定一致。",
            ),
        ],
    ),
    SyntheticDoc(
        title="培训管理制度",
        filename="培训管理制度.pdf",
        sections=[
            ("第一章 总则", "本制度规范公司内外部培训、费用报销与服务期约定。"),
            (
                "第二章 培训组织",
                "公司提供技能培训、入职培训与在职培训。培训时长与内容由HR统一安排。",
            ),
            (
                "第三章 费用与审批",
                "培训费用符合条件可报销，报销需走审批。外部培训需提交申请并落实预算。",
            ),
            (
                "第四章 服务期",
                "公司出资的专项培训需签署服务期协议，违反服务期约定按违约金条款执行。",
            ),
        ],
    ),
    SyntheticDoc(
        title="出差管理规定",
        filename="出差管理规定.pdf",
        sections=[
            ("第一章 总则", "本规定规范出差申请、补贴标准与费用报销。"),
            (
                "第二章 补贴与报销",
                "出差补贴按职级与城市标准执行，补贴金额见附表。出差报销需提交审批单与票据。",
            ),
        ],
    ),
    SyntheticDoc(
        title="试用期管理制度",
        filename="试用期管理制度.pdf",
        sections=[
            ("第一章 总则", "本制度规范试用期考核与转正流程。"),
            (
                "第二章 转正",
                "转正需提交转正申请，经过试用期评估与审批流程后生效。试用期考核结果作为转正依据。",
            ),
        ],
    ),
    SyntheticDoc(
        title="保密协议",
        filename="保密协议.pdf",
        sections=[
            (
                "第一条 协议效力",
                "保密协议经双方签署后具有法律效力，员工在职与离职后均负有保密义务。公司提供竞业限制补偿的员工适用本协议。",
            ),
            ("第二条 保密期限", "保密期限与竞业限制期限按协议条款约定执行。"),
        ],
    ),
]

SYNTHETIC_POLICY_DOCS: dict[str, SyntheticDoc] = {d.title: d for d in _DOCS}


def build_corpus() -> list[dict]:
    """Chunk every synthetic doc with the PRODUCTION section chunker.

    Returns chunk dicts in the same shape the ingestion pipeline produces
    (content/index/section/start_char/end_char + source filename), so the
    deterministic retrieval harness consumes identical structures.
    """
    from app.rag.ingestion.pipeline import Chunker

    chunker = Chunker()
    corpus: list[dict] = []
    for doc in _DOCS:
        chunks = chunker.chunk(doc.full_text(), strategy="section", source=doc.filename)
        for c in chunks:
            c["source"] = doc.filename
            c["doc_title"] = doc.title
            corpus.append(c)
    return corpus


# Golden label aliases: labels like 休假制度/薪酬制度/劳动合同 are treated as
# pointing at the canonical synthetic doc they clearly denote. Kept explicit
# and visible instead of hidden in matching heuristics.
LABEL_ALIASES: dict[str, str] = {
    "休假制度": "休假管理制度",
    "薪酬制度": "薪酬福利管理制度",
    "劳动合同": "劳动合同管理规定",
}


def resolve_label(label: str) -> str:
    """Map a golden citation label to its canonical synthetic doc title."""
    return LABEL_ALIASES.get(label, label)
