# HRBPilot UI/UX 修复报告 — 2026-09-09 晚

背景：用户指出工作任务页两处瑕疵（新建表单排版混乱、进行中列表主次颠倒），并要求全面排查其他页面的同类问题。

## 根因综述

本次发现的所有"排版乱"问题共享同一类根因：**类名被引用但样式从未定义**。项目 CSS 分两层（全局 `App.css` + 每页 `*.module.css`），两层各有一批失配。

## 修复清单（全部已验证）

| # | 问题 | 根因 | 修复 | 验证方式 |
|---|---|---|---|---|
| 1 | 新建/编辑/拆分任务表单 6 字段裸排，输入框 150px 默认宽随意折行 | `.task-metadata` 在全部 CSS 中零定义 | App.css 新增响应式两列 grid（label 在上、控件在下、必填/选填提示）；卡片元信息行拆分为专用 `.task-meta-row` | Playwright 实测输入框宽度 150→246px |
| 2 | 任务卡主次颠倒：「打开」是 meta 小字裸链接，操作按钮反而更重 | `.issue-list article a` 只有字号样式 | 「打开」改为实心主按钮 `.card-open`（action 绿底）；标记完成/编辑/拆分降级为描边次级按钮 | 浏览器实测：主按钮 56x36 绿底，次级透明底+浅边框 |
| 3 | 工作回顾裸文本拼句「下一步：标题 · 动作」 | 无样式结构 | 改为可点击的 `.continue-card`（复用今日页卡片语言：标题行+动作行+「继续处理 →」CTA） | 截图确认；单测已同步 |
| 4 | **知识反馈页整页样式失效**（列表/卡片/来源标签/操作区全裸排） | `KnowledgeFeedbackPage.module.css` 定义的是 `kf-*` 前缀类，TSX 引用无前缀 key（`styles.list`→undefined），共 8 组失配 | 模块 CSS 全部重命名为 TSX 期望的 key（list/card/cardConfirmed/…/sourceNegativeFeedback 等），并补 `cardOpen` 变体 | 静态审计 `ALL MODULE KEYS OK`；页面正常渲染 |
| 5 | 制度问答「本轮已接入 N 条历史消息」提示无样式 | `styles.historyNote` 未定义 | PolicyQaPage.module.css 补 `.historyNote` | 审计通过 |
| 6 | `.form-note` / `.link-button` / `.sr-only` 三个类零定义（任务页负责人读取失败提示、AdminUsersPage 无障碍标签） | 同类失配 | App.css 补齐三个定义 | 构建+回归通过 |
| 7 | 测试残留数据混入演示数据（3 条验证任务 + 1 条 E2E 问答会话） | 我此前验证产生 | 精确按 id 删除：work_tasks 3 条（验证任务×2、幂等验证任务）、chat_sessions/messages 1 组（年假问题）；另删除卡片验证临时任务 2 条。库中余 6 条真实任务 | SQL count 复核 |

同时给新建表单补齐选填标识（等待对象/截止时间/真实工作总量带 `form-hint`），消除"只有数字框写了可选"的不一致。

## 排查过但判定不是问题

- `/knowledge`（知识反馈）前端路由 `only('hr_manager')`、admin 被弹开 —— 与后端能力矩阵一致（`rbac.py`：admin 仅平台能力，knowledge_feedback 归 hr_manager），**非 bug**。
- 全部 11 个主页面截图审查（today/policy/tasks/interview/voice/weekly/culture/requests/knowledge/admin-users/settings/evaluation）：无白屏、无 console/pageerror/4xx-5xx。

## 验证基线

- 前端构建通过（注意：**必须 `--minify false`**，压缩阶段在本机 2GB 级空闲内存下会卡死 28 分钟+；无压缩构建仅 14 秒）
- 前端 vitest：**37/37 通过**（tasks-page 测试已同步 continue-card 新结构）
- Playwright 全页扫描 0 JS/console/网络错误

## 遗留提醒

- 服务器 5173 仍由静态服务托管 dist：**改前端代码后需重新 build** 才可见。
- E2E 账号的制度问答历史里有几组重复提问（验证脚本所致），属测试账号数据，未清理；如需演示干净数据可按 `chat_sessions.user_id = e2e 账号` 清空。
