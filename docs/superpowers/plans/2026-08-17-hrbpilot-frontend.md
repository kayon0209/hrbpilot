# HRBPilot Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a desktop-first React frontend for all five HRBPilot scenarios, backed only by the existing FastAPI APIs and their real state.

**Architecture:** Add an isolated `web/` Vite + React application. A typed API layer owns JWT refresh, error normalization, uploads, polling and SSE; feature folders own route-level UI. The FastAPI service remains the only business backend, while Vite proxies `/api` in development and the production web service proxies API traffic without exposing secrets to the browser.

**Tech Stack:** React 19, TypeScript, Vite, React Router, TanStack Query, Zustand, Zod, CSS Modules/CSS custom properties, Vitest, React Testing Library, Playwright, pnpm.

**Spec:** `docs/superpowers/specs/2026-08-17-hrbpilot-frontend-design.md`

**Execution status (2026-08-17):** Tasks 1–9 implemented. Verified with ESLint, 9 Vitest tests, TypeScript production build, Docker image build, live Nginx `/api/ready` proxy, and Chromium login-page acceptance. The authenticated Playwright flow remains environment-gated so credentials never enter source or command logs.

## Global Constraints

- Desktop-first at 1440px; 375px supports login, status viewing and lightweight policy Q&A only.
- Use the approved editorial workbench tokens: `#F7F7F4`, `#FFFFFF`, `#20231F`, `#6C716A`, `#245B4D`, `#A9652B`, `#B8473F`, `#E3E5DF`.
- Use Noto Serif SC for display, Noto Sans SC for UI/body and IBM Plex Mono for technical metadata; do not use Inter, Roboto, Arial or Space Grotesk.
- Do not invent history, notification, KPI, source, role or task data that the API does not return.
- Do not render or persist API keys, JWT secrets, database credentials or raw refresh tokens in components or logs.
- All authenticated HTTP requests carry `Authorization: Bearer <access token>`; streamed policy Q&A uses `fetch`, not `EventSource`.
- Every page implements loading, empty, processing, success, failure and unauthorized states applicable to its endpoint.
- Keep new frontend code under `web/`; create `web/AGENTS.md` before any frontend source files.

---

## File Structure

```text
web/
├── AGENTS.md                         # frontend conventions and cleanup rules
├── package.json                       # scripts and pinned project dependencies
├── vite.config.ts                     # dev proxy to FastAPI and Vitest config
├── index.html
├── src/
│   ├── api/                           # typed HTTP/SSE clients and endpoint adapters
│   ├── app/                           # router, providers, protected routes, session store
│   ├── components/                    # reusable shell, states, dialogs, status primitives
│   ├── features/                      # feature-local views and hooks
│   ├── pages/                         # route assembly only
│   ├── styles/                        # tokens, typography, global rules and motion
│   └── main.tsx
├── tests/                             # unit and component tests
└── e2e/                               # browser-level happy/error flows
```

Backend files only change in Task 9 for CORS/static deployment routing if the proxy cannot satisfy production serving. No route contract or database schema changes are permitted.

---

### Task 1: Create the isolated frontend foundation

**Files:**
- Create: `web/AGENTS.md`, `web/package.json`, `web/vite.config.ts`, `web/index.html`
- Create: `web/src/main.tsx`, `web/src/app/App.tsx`, `web/src/styles/tokens.css`, `web/src/styles/global.css`
- Create: `web/tests/setup.ts`, `web/tests/app-smoke.test.tsx`

**Interfaces:**
- Produces `App(): JSX.Element` mounted by `main.tsx`.
- Produces `VITE_API_BASE_URL` with default `""`; Vite proxies `/api` and `/openapi.json` to `http://localhost:8001`.

- [x] **Step 1: Write the failing smoke test**

```tsx
import { render, screen } from '@testing-library/react'
import { App } from '../src/app/App'

test('renders the frontend shell', () => {
  render(<App />)
  expect(screen.getByText('HRBPilot')).toBeInTheDocument()
})
```

- [x] **Step 2: Run the test to verify it fails**

Run: `corepack pnpm --dir web test --run tests/app-smoke.test.tsx`
Expected: failure because the frontend package and `App` do not exist.

- [x] **Step 3: Create project conventions and minimal implementation**

`web/AGENTS.md` must define feature-folder ownership, `*.module.css` naming, no mock business data, no secret logging, and deletion of `playwright-report/` and `test-results/` before commits. Add React, React Router, TanStack Query, Zustand, Zod, Vitest and Testing Library as project-local dependencies. Implement `App` with the approved title, token imports and a minimal visible shell.

```tsx
export function App() {
  return <main className="app-root"><span>HRBPilot</span></main>
}
```

- [x] **Step 4: Verify foundation checks**

Run: `corepack pnpm --dir web test --run tests/app-smoke.test.tsx && corepack pnpm --dir web build`
Expected: smoke test and production bundle pass.

- [x] **Step 5: Commit**

```bash
git add web
git commit -m "feat: scaffold HRBPilot frontend"
```

### Task 2: Implement typed authentication, API transport and route protection

**Files:**
- Create: `web/src/api/http.ts`, `web/src/api/auth.ts`, `web/src/api/types.ts`
- Create: `web/src/app/session-store.ts`, `web/src/app/ProtectedRoute.tsx`, `web/src/app/router.tsx`
- Create: `web/src/features/auth/LoginPage.tsx`, `web/src/features/auth/LoginPage.module.css`
- Test: `web/tests/api/http.test.ts`, `web/tests/auth/login-page.test.tsx`

**Interfaces:**
- `ApiClient.request<T>(path: string, init?: RequestInit): Promise<T>` throws `ApiError { status, code?, message, requestId? }`.
- `SessionStore.login(email: string, password: string): Promise<void>`; `logout(): void`; `user: UserProfile | null`.
- `ProtectedRoute` redirects an anonymous user to `/login` and renders an unauthorized explanation for an authenticated user without a required role.

- [ ] **Step 1: Write failing transport and login tests**

```ts
test('adds the bearer token and normalizes an API error', async () => {
  server.use(http.get('/api/auth/me', () => HttpResponse.json(
    { code: 'AUTH_ERROR', message: 'Missing token' }, { status: 401 },
  )))
  await expect(client.request('/api/auth/me')).rejects.toMatchObject({ status: 401 })
})
```

```tsx
test('submits email and password then opens the overview', async () => {
  render(<MemoryRouter initialEntries={['/login']}><App /></MemoryRouter>)
  await userEvent.type(screen.getByLabelText('邮箱'), 'hr@example.com')
  await userEvent.type(screen.getByLabelText('密码'), 'secret')
  await userEvent.click(screen.getByRole('button', { name: '登录' }))
  expect(await screen.findByRole('heading', { name: '概览' })).toBeVisible()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `corepack pnpm --dir web test --run tests/api/http.test.ts tests/auth/login-page.test.tsx`
Expected: failures because transport, store and routes do not exist.

- [ ] **Step 3: Implement auth and API contracts**

Implement `POST /api/auth/login`, `POST /api/auth/refresh` and `GET /api/auth/me`. Keep the access token in Zustand memory; store only the refresh token in `sessionStorage`, clear both on refresh failure, and never interpolate either token into error messages. Use Zod to parse successful auth responses and API error bodies.

- [ ] **Step 4: Verify transport and login flows**

Run: `corepack pnpm --dir web test --run tests/api/http.test.ts tests/auth/login-page.test.tsx`
Expected: bearer injection, refresh failure cleanup and successful login pass.

- [ ] **Step 5: Commit**

```bash
git add web/src web/tests
git commit -m "feat: add frontend authentication and API client"
```

### Task 3: Build the editorial application shell, navigation and shared states

**Files:**
- Create: `web/src/components/AppShell.tsx`, `web/src/components/AppShell.module.css`
- Create: `web/src/components/AsyncState.tsx`, `web/src/components/StatusBadge.tsx`, `web/src/components/PermissionNotice.tsx`
- Create: `web/src/pages/OverviewPage.tsx`, `web/src/pages/OverviewPage.module.css`
- Modify: `web/src/app/router.tsx`, `web/src/app/App.tsx`
- Test: `web/tests/components/app-shell.test.tsx`, `web/tests/pages/overview.test.tsx`

**Interfaces:**
- `AppShell({ children }: { children: ReactNode })` reads `SessionStore.user` and renders role-aware navigation.
- `AsyncState({ kind, title, detail, action }: AsyncStateProps)` supports `loading | empty | processing | success | error | unauthorized`.
- `getReadiness(): Promise<Readiness>` consumes `GET /api/ready`.

- [ ] **Step 1: Write failing navigation and health tests**

```tsx
test('marks HRBP-only pages as locked for hr_manager', () => {
  setSession({ role: 'hr_manager' })
  render(<AppShell><div /></AppShell>)
  expect(screen.getByText('面谈纪要')).toHaveAttribute('aria-disabled', 'true')
})
```

```tsx
test('shows a readable readiness failure', async () => {
  mockReady({ status: 'error', checks: { milvus: false } })
  render(<OverviewPage />)
  expect(await screen.findByText('向量检索服务不可用')).toBeVisible()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `corepack pnpm --dir web test --run tests/components/app-shell.test.tsx tests/pages/overview.test.tsx`
Expected: failure because shell and overview components do not exist.

- [ ] **Step 3: Implement shell and non-fabricated overview**

Create the 216px light navigation rail, 56px work bar, desktop grid and responsive collapse behavior. The overview uses only readiness checks and knowledge-base data; it must not show fabricated team or productivity KPIs. Map known dependency failures to readable text while retaining request id in an expandable diagnostic area.

- [ ] **Step 4: Verify shell behavior**

Run: `corepack pnpm --dir web test --run tests/components/app-shell.test.tsx tests/pages/overview.test.tsx && corepack pnpm --dir web build`
Expected: role gating, health error mapping and build pass.

- [ ] **Step 5: Commit**

```bash
git add web/src web/tests
git commit -m "feat: add editorial app shell and overview"
```

### Task 4: Implement policy Q&A with authenticated SSE and evidence display

**Files:**
- Create: `web/src/api/policy-qa.ts`, `web/src/features/policy-qa/usePolicyStream.ts`
- Create: `web/src/features/policy-qa/PolicyQaPage.tsx`, `EvidencePanel.tsx`, `PolicyQaPage.module.css`
- Test: `web/tests/features/policy-qa.test.tsx`, `web/tests/features/policy-stream.test.ts`

**Interfaces:**
- `listPolicyKnowledgeBases(): Promise<KnowledgeBaseSummary[]>` consumes `GET /api/policy-qa/knowledge-bases`.
- `streamPolicyAnswer(input, signal, onEvent): Promise<void>` posts `{ question, kb_id, stream: true }` to `/api/policy-qa/ask` and parses `data:` SSE frames.
- `PolicyStreamEvent` is `{ type: 'delta' | 'citation' | 'complete' | 'error'; data: unknown }`.

- [ ] **Step 1: Write failing SSE and evidence tests**

```ts
test('parses fragmented SSE frames and emits answer deltas', async () => {
  const events: PolicyStreamEvent[] = []
  await streamPolicyAnswer(input, new AbortController().signal, event => events.push(event))
  expect(events).toContainEqual(expect.objectContaining({ type: 'delta' }))
})
```

```tsx
test('shows semantic, keyword and combined evidence labels', async () => {
  render(<PolicyQaPage />)
  expect(await screen.findByText('语义相关')).toBeVisible()
  expect(screen.getByText('条款关键词')).toBeVisible()
  expect(screen.getByText('综合排序')).toBeVisible()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `corepack pnpm --dir web test --run tests/features/policy-stream.test.ts tests/features/policy-qa.test.tsx`
Expected: failure because stream parser and page do not exist.

- [ ] **Step 3: Implement the real Q&A flow**

Render knowledge-base selection, a question field, abortable stream state, answer sections and the evidence panel. Map technical retrieval names only in source cards: `dense` to `语义相关`, `sparse` to `条款关键词`, `hybrid` to `综合排序`. Show source filename and excerpt only when returned. Submit feedback with `/api/policy-qa/feedback`; show the current-session history limitation instead of an invented chat list.

- [ ] **Step 4: Verify stream success, abort and failure paths**

Run: `corepack pnpm --dir web test --run tests/features/policy-stream.test.ts tests/features/policy-qa.test.tsx`
Expected: fragmented frames, user abort, 401 and backend error cases pass.

- [ ] **Step 5: Commit**

```bash
git add web/src web/tests
git commit -m "feat: add streamed policy question workspace"
```

### Task 5: Implement knowledge-base, upload and indexing workflows

**Files:**
- Create: `web/src/api/knowledge-base.ts`
- Create: `web/src/features/knowledge-base/KnowledgeBasePage.tsx`, `DocumentUploader.tsx`, `DocumentTable.tsx`, `KnowledgeBasePage.module.css`
- Test: `web/tests/features/knowledge-base.test.tsx`, `web/tests/features/document-uploader.test.tsx`

**Interfaces:**
- `createKnowledgeBase(input)`, `listKnowledgeBases()`, `getKnowledgeBase(id)`, `uploadDocument(kbId, file)`, `triggerIngestion(kbId)`, `listDocuments(kbId)`, `deleteDocument(kbId, docId)`.
- `DocumentStatus = 'uploaded' | 'parsing' | 'indexed' | 'error'`.
- `useDocumentPolling(kbId, active: boolean)` refetches every 2 seconds only while a document is `uploaded` or `parsing`.

- [ ] **Step 1: Write failing upload and status tests**

```tsx
test('accepts only real backend file types and the 20 MB size ceiling', async () => {
  render(<DocumentUploader kbId="kb-1" />)
  await userEvent.upload(screen.getByLabelText('上传制度文件'), new File(['x'], 'policy.xls'))
  expect(screen.getByText('仅支持 TXT、PDF、DOCX')).toBeVisible()
})
```

```tsx
test('renders an indexing error from the backend without hiding the reason', () => {
  render(<DocumentTable documents={[{ status: 'error', error_message: 'Invalid token' }]} />)
  expect(screen.getByText('Invalid token')).toBeVisible()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `corepack pnpm --dir web test --run tests/features/knowledge-base.test.tsx tests/features/document-uploader.test.tsx`
Expected: failure because adapters and feature components do not exist.

- [ ] **Step 3: Implement the directory-detail workflow**

Build a knowledge-base list, creation dialog, selected detail panel, multipart upload, explicit `开始索引` action and 2-second status polling. Present `uploaded`, `parsing`, `indexed` and `error` using the approved copy. Do not automatically claim upload equals index completion. Deletions use a confirmation dialog naming the file or knowledge base exactly.

- [ ] **Step 4: Verify workflow states**

Run: `corepack pnpm --dir web test --run tests/features/knowledge-base.test.tsx tests/features/document-uploader.test.tsx`
Expected: validation, multipart upload, retryable error and indexed states pass.

- [ ] **Step 5: Commit**

```bash
git add web/src web/tests
git commit -m "feat: add knowledge base indexing workspace"
```

### Task 6: Implement asynchronous analysis workbenches for interview and voice insight

**Files:**
- Create: `web/src/api/async-scenarios.ts`, `web/src/features/async-workbench/useTaskPolling.ts`
- Create: `web/src/features/interview/InterviewDigestPage.tsx`, `web/src/features/voice/VoiceInsightPage.tsx`
- Test: `web/tests/features/async-workbench.test.tsx`, `web/tests/features/interview.test.tsx`, `web/tests/features/voice.test.tsx`

**Interfaces:**
- `startInterviewAnalysis(content): Promise<{ task_id: string; status: string }>` and `getInterviewProgress(taskId)`.
- `startVoiceAnalysis(input)` and `getVoiceProgress(taskId)` follow the same `TaskProgress { status, progress, error }` contract.
- `useTaskPolling<T>(taskId, getProgress, getResult): { phase, progress, result, error, retry }` stops at completed or error.

- [ ] **Step 1: Write failing polling and validation tests**

```tsx
test('does not submit interview content shorter than 50 characters', async () => {
  render(<InterviewDigestPage />)
  await userEvent.type(screen.getByLabelText('面谈内容'), '太短')
  await userEvent.click(screen.getByRole('button', { name: '开始分析' }))
  expect(screen.getByText('至少需要50字')).toBeVisible()
})
```

```ts
test('stops polling after a completed result', async () => {
  const { result } = renderHook(() => useTaskPolling('t-1', getProgress, getResult))
  await waitFor(() => expect(result.current.phase).toBe('completed'))
  expect(getProgress).toHaveBeenCalledTimes(1)
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `corepack pnpm --dir web test --run tests/features/async-workbench.test.tsx tests/features/interview.test.tsx tests/features/voice.test.tsx`
Expected: failure because polling hook and scenario pages do not exist.

- [ ] **Step 3: Implement reusable task presentation**

Implement upload/paste → preview parsed text → start analysis → poll every 2 seconds → result flow for interview. Implement text/transcript input and report flow for voice. Show a locked permission state for `hr_manager`, real errors from the API and a clear unavailable-history empty state.

- [ ] **Step 4: Verify async lifecycle**

Run: `corepack pnpm --dir web test --run tests/features/async-workbench.test.tsx tests/features/interview.test.tsx tests/features/voice.test.tsx`
Expected: pending, running, completed, error, cancellation and role-gated flows pass.

- [ ] **Step 5: Commit**

```bash
git add web/src web/tests
git commit -m "feat: add interview and voice workbenches"
```

### Task 7: Implement weekly-report and culture-content creation workbenches

**Files:**
- Create: `web/src/api/content-workflows.ts`
- Create: `web/src/features/weekly/WeeklyReportPage.tsx`, `web/src/features/culture/CultureContentPage.tsx`
- Test: `web/tests/features/weekly-report.test.tsx`, `web/tests/features/culture-content.test.tsx`

**Interfaces:**
- `generateWeeklyReport({ period, source_ids, draft_mode })`, `saveWeeklyReport({ report_id, action })`.
- `expandKeywords(input)`, `generateCultureContent(input)`.
- `ReportAction = 'save' | 'publish'`; publish always requires an explicit confirmation dialog.

- [ ] **Step 1: Write failing report and culture tests**

```tsx
test('marks auto-generated report sources as examples when no source ids exist', async () => {
  render(<WeeklyReportPage />)
  await userEvent.click(screen.getByRole('button', { name: '生成周报' }))
  expect(await screen.findByText('将使用示例来源生成草稿')).toBeVisible()
})
```

```tsx
test('keeps keyword expansion separate from content generation', async () => {
  render(<CultureContentPage />)
  expect(screen.getByRole('button', { name: '扩展关键词' })).toBeVisible()
  expect(screen.getByRole('button', { name: '生成内容' })).toBeVisible()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `corepack pnpm --dir web test --run tests/features/weekly-report.test.tsx tests/features/culture-content.test.tsx`
Expected: failure because adapters and pages do not exist.

- [ ] **Step 3: Implement report-paper and content-editor layouts**

Build the editable report paper with source list, period selection, save draft and publish confirmation. Mark in-memory storage limitations and example source usage. Build the culture two-step parameter/editor layout without a fake template marketplace or collaboration data.

- [ ] **Step 4: Verify authoring flows**

Run: `corepack pnpm --dir web test --run tests/features/weekly-report.test.tsx tests/features/culture-content.test.tsx`
Expected: example notice, save/publish distinction, keyword expansion and generation error states pass.

- [ ] **Step 5: Commit**

```bash
git add web/src web/tests
git commit -m "feat: add weekly report and culture workbenches"
```

### Task 8: Implement evaluation, provider settings and responsive/accessibility review

**Files:**
- Create: `web/src/api/evaluation.ts`, `web/src/api/settings.ts`
- Create: `web/src/features/evaluation/EvaluationPage.tsx`, `web/src/features/settings/SettingsPage.tsx`
- Modify: `web/src/styles/global.css`, route files
- Test: `web/tests/features/evaluation.test.tsx`, `web/tests/features/settings.test.tsx`, `web/tests/a11y.test.tsx`

**Interfaces:**
- `getMetrics()`, `getScenarioTrend(scenarioId)`, `getProvider()`, `testProvider()`, `switchProvider(provider)`.
- `MetricCard` renders only API-returned values and accepts `isStub?: boolean`.

- [ ] **Step 1: Write failing honesty and accessibility tests**

```tsx
test('labels a stub evaluation as unsuitable for business decisions', () => {
  render(<MetricCard value={0.7} isStub />)
  expect(screen.getByText('不可用于业务判断')).toBeVisible()
})
```

```tsx
test('has no critical axe violations in the desktop shell', async () => {
  const { container } = render(<AppShell><OverviewPage /></AppShell>)
  expect(await axe(container)).toHaveNoViolations()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `corepack pnpm --dir web test --run tests/features/evaluation.test.tsx tests/features/settings.test.tsx tests/a11y.test.tsx`
Expected: failure because pages and accessible labels do not exist.

- [ ] **Step 3: Implement truthful metrics and settings**

Render empty states for missing metrics and a visible stub label when the backend identifies placeholder values. Render provider status and explicit connectivity test; exclude all credential fields. Add keyboard focus styles, semantic landmarks, 44px action targets and responsive navigation collapse.

- [ ] **Step 4: Verify quality checks**

Run: `corepack pnpm --dir web test --run tests/features/evaluation.test.tsx tests/features/settings.test.tsx tests/a11y.test.tsx && corepack pnpm --dir web build`
Expected: metrics honesty, settings and accessibility tests pass; bundle builds.

- [ ] **Step 5: Commit**

```bash
git add web/src web/tests
git commit -m "feat: add evaluation and provider settings pages"
```

### Task 9: Add development/production delivery and verify live backend integration

**Files:**
- Modify: `docker-compose.yml`, `.gitignore`, `README.md`
- Create: `web/Dockerfile`, `web/nginx.conf`, `web/e2e/auth-policy-kb.spec.ts`
- Modify only if required by browser test: `app/access/middleware/cors.py`

**Interfaces:**
- Compose service `web` exposes port `3000`, serves static assets and reverse-proxies `/api` to `app:8000`.
- Browser test uses an account created by the existing local bootstrap process; credentials are passed only through uncommitted local environment variables.

- [ ] **Step 1: Write the failing browser acceptance flow**

```ts
test('logs in, opens the knowledge base, and asks a policy question', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('邮箱').fill(process.env.E2E_EMAIL!)
  await page.getByLabel('密码').fill(process.env.E2E_PASSWORD!)
  await page.getByRole('button', { name: '登录' }).click()
  await page.getByRole('link', { name: '知识库' }).click()
  await expect(page.getByText('默认制度知识库')).toBeVisible()
  await page.getByRole('link', { name: '制度问答' }).click()
  await page.getByLabel('问题').fill('请假超过三天如何审批？')
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.getByText('依据与来源')).toBeVisible()
})
```

- [ ] **Step 2: Run the browser test to verify it fails**

Run: `corepack pnpm --dir web exec playwright test e2e/auth-policy-kb.spec.ts`
Expected: failure because the web service and browser route do not exist.

- [ ] **Step 3: Add deployment integration without exposing secrets**

Add a multi-stage web Dockerfile and Nginx proxy. Add the Compose `web` service with no API keys or database credentials. Update the README with local commands, `http://localhost:3000`, required user-created login and troubleshooting. Modify CORS only if direct dev requests require it; allow exact development origins, never wildcard credentials.

- [ ] **Step 4: Run final verification**

Run: `corepack pnpm --dir web lint && corepack pnpm --dir web test --run && corepack pnpm --dir web build`
Expected: lint, unit/component tests and production build pass.

Run: `docker compose up -d --build web app worker`
Expected: app, worker and web are healthy; web is reachable on port 3000.

Run: `corepack pnpm --dir web exec playwright test e2e/auth-policy-kb.spec.ts`
Expected: login, knowledge-base view, policy question and evidence panel pass against the live backend.

- [ ] **Step 5: Commit**

```bash
git add web docker-compose.yml README.md .gitignore app/access/middleware/cors.py
git commit -m "feat: deliver HRBPilot web workbench"
```

## Spec Coverage Review

- Desktop editorial tokens, typography, motion and anti-patterns: Tasks 1 and 3.
- Login, session, RBAC and role-aware navigation: Tasks 2 and 3.
- Real streamed policy Q&A and evidence: Task 4.
- Knowledge-base upload, indexing, failures and document lifecycle: Task 5.
- Interview, voice, weekly report and culture content: Tasks 6 and 7.
- Evaluation, provider health, empty/stub honesty and settings: Task 8.
- Development proxy, production delivery, responsive/accessibility checks and real end-to-end verification: Task 9.

## Plan Self-Review

- Placeholder scan: no unresolved placeholder steps remain.
- Interface consistency: all feature pages depend on adapters introduced in their own or earlier tasks; common task polling is introduced before interview/voice pages consume it.
- Scope: all five scenarios share the foundation but are independently testable; no database schema, secret, CI/CD or backend business-contract change is planned.
