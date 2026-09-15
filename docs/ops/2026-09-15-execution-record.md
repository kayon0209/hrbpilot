# MCP P0 执行记录（2026-09-15）

- 分支：`codex/external-assistant-mcp`（基线 `9d4375d`）
- 执行机：Windows（本仓库位于 `D:\demo\hrbpilot`，纯 ASCII 路径）
- 任务书：`docs/plans/2026-09-15-mcp-p0-development-spec.md`
- 原则：每个任务记录实际命令与实际输出；未通过的项**如实标注**，不写"应该没问题"。

## 环境事实（开工前现场确认）

| 项 | 结果 |
| --- | --- |
| 仓库位置 | `D:\demo\hrbpilot`（任务书建议 `C:\work`；本机已有完整 clone + venv + 真实 key 的 `.env`，改用现仓库，纯 ASCII 路径满足任务书约束） |
| 分支拓扑 | `origin/codex/external-assistant-mcp` = `main`(868c6ae) + 32 个 MCP 提交；检出无损失 |
| Docker Desktop | **引擎起不来**（详见 T1） |
| PowerShell | **本机损坏**（`System, Version=4.0.0.0` 程序集加载失败，所有 powershell 调用实际未执行）；改用 `cmd.exe` / Git Bash |
| codex CLI | **本机未安装**；从 npm 拉取 `@openai/codex@0.154.0-win32-x64` 二进制完成现场确认（见 T2） |
| WorkBuddy | 桌面端已安装（开始菜单 `WorkBuddy.lnk`）；CLI 无 `mcp login`（与任务书一致） |

---

## T1 环境准备与首次启动

### 已完成

```bash
# 签名密钥（P-256，ES256 用）
openssl ecparam -genkey -name prime256v1 -noout -out C:\work\as-signing.pem   # 232 字节
openssl base64 -A -in C:\work\as-signing.pem -out C:\work\as-signing.b64       # 312 字节单行

# .gitignore 兜底（已提交）
printf '\n# secrets: key material (HRBPILOT MCP P0)\n*.pem\n*.b64\n' >> .gitignore
git check-ignore -v env.docker .env   # 两者本就在忽略清单（.gitignore:20 / :16）
```

`env.docker` 由 `env.docker.example` 生成并做了必改项替换：
`APP_ENV=development`、`JWT_SECRET=53 位`（复用本机 `.env` 真实值）、
`LLM_API_KEY` / `EMBEDDING_API_KEY`（本机真实 key）、`MINIO_*`（默认值，compose 联动一致）、
`OAUTH_SIGNING_KEY_PEM=<C:\work\as-signing.b64 单行>`。
`OAUTH_ENABLE_DYNAMIC_REGISTRATION` 未改（compose 已为 oauth-as 强制 `true`，与任务书 §2 一致）。

### 未通过：Docker 引擎起不来（**阻塞中，非绕过**）

原始错误（`com.docker.backend.exe.log`）：

```
wsl: w:<HOME>\.wslconfig 涓殑杞箟瀛楃?"2"?鏃犳晥        ← .wslconfig 解析错误
Wsl/Service/AttachDisk/MountDisk/HCS/E_ACCESSDENIED: 无法将磁盘
"\\?\D:\docker Resource\DockerDesktopWSL\disk\docker_data.vhdx" 附加到 WSL2: 拒绝访问。
```

排查过程（全部有实测依据）：

1. `~/.wslconfig` 头部有 UTF-8 BOM + `[wsl2]` 节下有非法键 `root = D:\wsl`（`root` 属于
   `[automount]` 且值应为 Linux 路径）→ **已修复**（备份至 `~/.wslconfig.bak-20260915`，
   移除 BOM 与非法键）。修复后 WSL 解析错误消失。
2. 修复后仍是 `E_ACCESSDENIED`：对比 D 盘 `ext4.vhdx` ACL —— 只有 `BUILTIN\Administrators`/
   `SYSTEM`/`Authenticated Users(M)` 与若干 `S-1-5-83-*`（VHUX 托管账户），**无当前用户**；
   `wsl --mount --bare` 手动复现同样被拒 → 挂载需要管理员令牌。
3. 尝试触发 UAC 提权（`wscript ShellExecute "runas"`）—— **弹窗需用户在屏幕上确认**，
   执行者无法代点。`Docker Desktop.exe` 非提权启动 23 秒后 backend 退出。

**T1 验收状态：未完成（阻塞于 UAC）**。已就绪的产物：密钥、`env.docker`、`.gitignore`。
**恢复路径**：用户右键"以管理员身份运行" Docker Desktop（或确认 UAC 弹窗）后：
`docker compose up -d --build` → 四容器 healthy → 三个端点 200 → `oauth_signing_key_ephemeral` 计数 0。

---

## T2 scope 套餐与连接冒烟

### 步骤 1：CLI 现场确认（原始输出要点）

本机无 codex 安装，从 npm 取**同版本** win32 二进制执行：

```bash
npm pack @openai/codex@0.154.0-win32-x64   # 与 macOS 实测记录同版本
./codex.exe --version                       # codex-cli 0.154.0
./codex.exe mcp add --help
# Options: --url, --bearer-token-env-var, --oauth-client-id,
#          --oauth-client-registration <AUTO|CIMD|DCR>, --oauth-resource
#（mcp add 无 scope 参数）
./codex.exe mcp login --help
# Options: --scopes <SCOPE,SCOPE>  ← login 支持！逗号分隔
```

**结论：路线 A 成立** —— `codex mcp login <name> --scopes hrb:profile:read,hrb:policy:read,...`

### 任务书与源码的一处事实差异（对账，非推翻）

任务书断言"未指定 scope → 仅基线"。源码实测：

- `app/oauth/clients.py:253-256`：DCR/CIMD 注册**未带 scope** → 注册范围 = **全部 5 项**（不是基线）；
- `app/oauth/routes/authorize.py:201-205`（改造前）：授权请求未带 scope → **客户端注册全集**。

即"只拿到基线"的成因不在 AS 默认链，而是客户端**显式申请了窄 scope**（codex login 不传
`--scopes` 时按其内置默认申请）。因此**路线 A（显式 `--scopes`）是主修**；路线 B（`OAUTH_DEFAULT_SCOPE`）按任务书一并实现（见下），安全边界逐条落地。

### 已交付（commit `c611cf4`）

1. **`OAUTH_DEFAULT_SCOPE`**（`app/config/settings.py`）：
   - 空（默认）= 维持既有回退（客户端注册全集）；
   - 配置后 = 无 scope 授权请求的**申请**套餐；启动期校验拼错值（`validate_oauth_default_scope`）；
   - **不得**超出客户端注册范围（authorize 侧交集）；**未绑定租户的 DCR/CIMD 客户端剔除 `hrb:case:propose`**；
   - 注释写明：默认 scope 不绕过 `authorize_tool_call` 的三重交集（角色能力 ∩ 令牌 scope ∩ 客户端上限）。
2. **`scripts/connect-codex-local-mcp.ps1`**（UTF-8 BOM，PS 5.1/7 兼容）：
   套餐选择（1=仅查询 / 2=查询+办理，写权限不默认）→ `Invoke-RestMethod` 就绪检查 → 复用/新增
   `codex mcp add`（DCR）→ `codex mcp login --scopes <逗号串>` → **三项冒烟自动执行**：
   ① `get_my_access_profile`（身份 + `effective_scopes` 与申请套餐逐项比对，缺哪项点名）
   ② `search_policy` 固定问句"公司的年假是怎么规定的"必须带出处。
3. 测试：`tests/oauth/test_default_scope.py` 7 条（未配置回退 / 配置生效 / 不超客户端注册 /
   未绑定租户剔除写权限 / 已绑定不受影响 / 显式 scope 优先 / 拼错启动报错）。

### 验收状态

- 代码与测试：**通过**（`tests/oauth/` 119 passed 含 7 条新增；ruff/mypy 绿）。
- D3/D4/D5 实机（Codex 自然语言、WorkBuddy 桌面端）：**待 Docker 引擎与用户配合**，
  未执行，不宣称通过。ps1 脚本本机无法执行（PowerShell 运行时损坏），需在正常机器验证。

---

## T3 工具契约（commit `8a65958`）

改动：

- `app/mcp/server.py`：11 个工具全部 `annotations=ToolAnnotations(...)`（从 catalog **派生**而非
  手写：读 `read_only_hint=True`；写 `False` + `destructive_hint=True` + `idempotent_hint=True`；
  全部 `open_world_hint=False`）+ `structured_output=True`；全部入参 schema 收紧
  `additionalProperties: false`。
- `app/mcp/tool_descriptions.py`（新）：11 条三段式中文描述（何时用/何时不用/真实例子），
  集中存放带版本号 `DESCRIPTIONS_VERSION="2026-09-15.1"`；`_describe()` 对漏登记的新工具快速失败。
- `app/mcp/contract.py`：`failure_envelope` 增 `fix`（怎么改）与 `retryable`；
  `FAILURE_HINTS` 登记每个错误码的自纠路径；未登记的诚实给 `fix=None, retryable=False`。
- INVALID_PARAMS 的 `detail` 携带 pydantic 校验事实（哪个字段、什么约束），不透堆栈。

验收输出：

```
$ pytest tests/mcp/test_tool_contract.py -q
6 passed
$ pytest tests/mcp/ -q
176 passed          # 全目录无回归
$ pytest tests/mcp/test_tool_contract.py tests/mcp/test_mcp_contract.py -q
24 passed
```

工具注册冒烟（11 个全部）：

```
search_policy            ro=True de=False ... out_schema=yes desc=ok
create_hr_case           ro=False de=True ... out_schema=yes desc=ok
（11 行全部符合预期；协议层 list_tools 可见 readOnlyHint/destructiveHint）
```

**T3 验收：通过**（单测断言 annotations 完整性与写工具 read_only=False；客户端侧字段
透出已由冒烟证实；"模型据此自纠"的实机验证随 D3 一并待 Docker）。

---

## T4 结果侧上下文预算（commit `439e50b`）

改动：

- `search_cases`：schema `limit ≤20`（ge=1 le=20）+ `offset`（0–980）+ `detail`；
  executor `limit+1` 探测 `has_more`，返回 `next_cursor` 与 `pagination_note`
  （"还有更多未显示…把 next_cursor=N 作为 offset 回传…或用 status/category 缩小范围"）。
- `search_policy` / `get_policy_source`：`detail: Literal["concise","detailed"]`，默认 concise
  （出处+片段，单片段 300 字，不带 chunk_id/document_id）；detailed 才带后续调用需要的 id 与未截断正文。
- `_case_view`：两档都保留 `case_id`（后续调用的必要键）；detailed 额外带 `created_at`；
  人类可读字段（title/subject_ref/category/risk_level/status）为主。
- `app/mcp/budgets.py`：`cases` 列表上限独立为 20（对齐 schema），chunks 维持 5 —— 修掉了
  "信封层把显式请求的 20 条砍成 5 条 + 一句截断提示"的冲突。
- **顺手修了一个真实 bug**：`read_executors._require_principal` 运行时 `isinstance` 引用
  `TYPE_CHECKING` 下的名字 → 任何走 `execute_search_cases` 的调用都会 NameError →
  INTERNAL_ERROR。改为函数内局部导入。T4 测试第一次全红正是它暴露的。

验收输出：

```
$ pytest tests/mcp/test_result_budgets.py -q
8 passed        # 分页上限20 / 截断留痕(has_more+cursor+引导) / 末页无cursor /
                # cursor回传翻页不重叠 / concise<detailed 体积差 / 默认concise /
                # 可读字段优先 / schema上限20
$ pytest tests/mcp/ tests/access/ -q
207 passed, 1 failed → 修断言（既有测试精确比对执行器入参，现在多了 detail 默认值）
$ pytest tests/mcp/test_mcp_contract.py -q   （更新断言后）
18 passed
```

**T4 验收：通过**（concise vs detailed 体积差在测试里有量化断言：300 字截断 vs
600 字总闸 + id 字段有无；列表默认不超过 20 由 schema 与测试双锁）。

---

## T5 前端漂移修复（commit `d013d1a`）

改动（三处，与任务书 T5 表格逐项对应）：

1. `web/src/features/mcp/toolCopy.ts`：删除 `hrbpilot_ping` 条目（5 行）；
2. `web/src/features/mcp/McpPage.tsx`：删除 EXAMPLES 里的 `hrbpilot_ping: {}`；
3. `McpPage.tsx` 匿名文案改为："业务查询与办理都需要登录后携带身份凭证；未登录时本页的
   「在线体验」只会返回需要登录的提示，不会返回真实数据。仅工具清单这类公开信息可以匿名查看。"

验收输出（实际执行）：

```
$ corepack pnpm exec vitest run tests/features/mcp-plain-language.test.tsx
Test Files  1 passed (1)   Tests  16 passed (16)      # 时长 19.62s
$ corepack pnpm build
✓ 239 modules transformed ... ✓ built in 2.73s
```

仓库内已无 `hrbpilot_ping` 引用（`grep -rn` 唯一命中是测试里 `queryByText('hrbpilot_ping')`
应为 null 的**防护断言**，保留）。

**T5 验收：通过**（D8 满足：web 单测 + build 全绿，文案与后端行为一致）。

---

## T6 评测基线（commit `c5ec289`）

交付物：

1. `evaluation/tool_routing_golden.jsonl`：56 条（任务书起点 20 条全部收录并扩充），
   train 48 / held-out 8；四类期望全覆盖（tool 33 / clarify 7 / refuse 7 / multi 5 + clarify 型 4）；
   11 个工具各有至少一条正例。
2. `scripts/eval_tool_routing.py`：客户端位置的真实评测器 —— DCR+PKCE 拿真实令牌 →
   真实 `tools/list` 拉目录（不维护第二份描述）→ 真实 LLM 路由判断 → 读工具真调一次
   验证信封契约（写工具不真调，不制造审批记录）。指标与任务书一一对应，
   train/held-out 分列，明细落 `evaluation/results/tool_routing_<stamp>.jsonl`。
3. `docs/ops/2026-09-15-tool-routing-baseline.md`：判分规则 + held-out 用途已写实；
   **真实数字标注"待补"**（Docker 阻塞，见 T1）。
4. `tests/evaluation/test_tool_routing_golden.py`：14 条结构测试（≥50 条、id 唯一、
   expect 合法、held-out ≥10%、四类覆盖、每工具有正例、判分语义含"未验证≠失败"）。

验收输出：

```
$ pytest tests/evaluation/test_tool_routing_golden.py -q
14 passed
```

**T6 验收：部分通过** —— 黄金集/评测器/结构测试全部落地；**首个真实端点基线数字未产出**
（阻塞于 Docker，报告里如实标注，不宣称完成 D7）。
补跑命令（引擎恢复后）：

```
docker compose --profile test run --rm test \
    python scripts/eval_tool_routing.py --as-url http://oauth-as:8000 --mcp-url http://app:8000/mcp
```

---

## T7 门禁

（本节在全量门禁跑完后回填；Docker 版门禁待引擎恢复后补跑对账）

---

## 提交清单（一个任务一个提交）

| commit | 任务 |
| --- | --- |
| `d013d1a` | T5 fix(web): 移除 hrbpilot_ping 并修正匿名试用文案（含任务书落盘与 .gitignore 兜底） |
| `8a65958` | T3 feat(mcp): 工具契约透出 annotations 与结构化输出 |
| `439e50b` | T4 feat(mcp): 结果侧分页、截断引导与摘要分档 |
| `c611cf4` | T2 fix(oauth): 授权请求支持 scope 套餐并在连接时校验（Windows 接入脚本） |
| `c5ec289` | T6 test(eval): 中文口语工具路由黄金集与基线脚本 |

推送前核对：`env.docker` / `as-signing.pem` / `as-signing.b64` 均不在 git 状态里
（`git status --short` 只含预期文件；密钥生成在仓库外的 `C:\work`）。

## 明确不做（遵守任务书 §9）

未做 execute_anything / meta-tool / A2A / MCP Tasks / EMA / MCP Apps / 第三方连接器；
未改动 `authorize_tool_call` 判定语义（T2 的默认 scope 只影响"申请什么"，
判定链一处未动，有既有测试 176+ 守护）。
