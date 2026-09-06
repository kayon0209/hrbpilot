# E2E 运行说明

Playwright 用例需要：真实后端（真库）、真实前端、四角色测试账号。全部通过**进程环境变量**注入，不改 `.env`。

## 必需的环境变量

```bash
# 四角色测试账号（JSON，全部走真实 DB bcrypt 登录链路）
export E2E_ROLE_ACCOUNTS='{
  "employee":   {"email": "employee@hrbpilot.local", "password": "<开发密码>"},
  "hrbp":       {"email": "hrbp@hrbpilot.local",     "password": "<开发密码>"},
  "hr_manager": {"email": "demo01@163.com",           "password": "<开发密码>"},
  "admin":      {"email": "admin@hrbpilot.local",     "password": "<开发密码>"}
}'

# admin 知识库旅程（auth-policy-kb.spec.ts 的 admin 用例）
export E2E_EMAIL="admin@hrbpilot.local"
export E2E_PASSWORD="<开发密码>"
```

缺账号的用例会 `test.skip` 并注明原因——skip 不是通过。

## 可选变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `E2E_BASE_URL` | `http://localhost:5173` | 前端地址（vite 默认端口） |
| `E2E_API_TARGET` | `http://localhost:8001` | vite 代理的后端地址 |

## 运行步骤

```bash
# 1. 后端（注意：本机若有同端口旧进程请先确认其代码版本）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001

# 2. 前端（代理指向后端）
cd web && E2E_API_TARGET="http://127.0.0.1:8001" corepack pnpm exec vite --port 5173 --strictPort

# 3. 测试
corepack pnpm exec playwright test
```

### 可复现的隔离库账号播种

不要复用开发库账号，也不要把测试密码写入 `.env`。对已证明隔离的测试库，可由
`scripts/seed_e2e_role_accounts.py` 创建一次性四角色 bcrypt 账号。调用方必须在
当前进程提供随机 `E2E_RUN_ID`（6–16 位小写字母/数字）与至少 16 位的
`E2E_SEED_PASSWORD`；脚本不会输出密码。账号邮箱格式为
`e2e-<role>-<run_id>@hrbpilot.test`，随后由同一进程拼出 `E2E_ROLE_ACCOUNTS`。

示例（仅限隔离库）：

```bash
E2E_RUN_ID=abc123 E2E_SEED_PASSWORD='<random>' \\
DATABASE_URL='<isolated database URL>' \\
python scripts/seed_e2e_role_accounts.py
```

## 已知注意事项

1. **限流**：全套 ~25 用例密集请求会触发 `user 30/min` 限流（429「请求过于频繁」）。
   测试运行时给后端进程加环境变量调高阈值（生产阈值不变）：
   `RATE_LIMIT_USER_PER_MINUTE=600 RATE_LIMIT_TENANT_PER_MINUTE=1200`
2. **真实 LLM 流式**：制度问答用例等待真实流式回答，spec 内已设 180s 超时；
   需要后端 LLM/检索配置可用（Gitee AI / Milvus）。
3. **遗留进程**：跑之前确认 5173/8001 端口上的进程确实是当前工作树代码，
   否则会出现「页面是旧版本」的假失败；也可像上面步骤一样换端口并同步改
   `E2E_BASE_URL` / `E2E_API_TARGET`。
4. **测试数据**：任务旅程（tasks-journey）会创建带唯一时间戳的 `E2E 多日任务`，
   长期运行后可用前缀清理（先子后父，父有自引用外键）。
