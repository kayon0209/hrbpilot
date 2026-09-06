import { expect, test } from '@playwright/test'

type RoleAccount = { email: string; password: string }

function roleAccounts() {
  const raw = process.env.E2E_ROLE_ACCOUNTS
  return raw ? JSON.parse(raw) as Partial<Record<'employee' | 'hrbp', RoleAccount>> : {}
}

async function login(page: Parameters<Parameters<typeof test>[1]>[0]['page'], account: RoleAccount) {
  await page.goto('/')
  await page.getByLabel('邮箱').fill(account.email)
  await page.getByLabel('密码').fill(account.password)
  await page.getByRole('button', { name: '登录' }).click()
}

async function askPolicyQuestion(page: Parameters<Parameters<typeof test>[1]>[0]['page']) {
  await expect(page.getByRole('heading', { name: '制度问答' })).toBeVisible()
  await expect(page.getByLabel('知识库')).toBeEnabled()
  await page.getByLabel('问题').fill('请假超过三天需要经过哪些审批？')
  await page.getByRole('button', { name: '发送问题' }).click()

  await expect(page.getByRole('button', { name: '有帮助' })).toBeVisible({ timeout: 120_000 })
  // The evidence panel renders "依据与来源" and its article list as siblings
  // inside one section — assert against the section, not by DOM hops.
  const evidenceSection = page.getByRole('heading', { name: '依据与来源' }).locator('xpath=ancestor::section[1]')
  await expect(
    evidenceSection.locator('article').first().or(
      evidenceSection.getByText('当前资料中找不到依据。可以：换一种问法、上传相关制度，或交给 HR 复核。'),
    ),
  ).toBeVisible()
}

test('shows the production login experience', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('HRBPilot', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '进入工作台' })).toBeVisible()
  await expect(page.getByLabel('邮箱')).toBeEditable()
  await expect(page.getByLabel('密码')).toBeEditable()
})

test('admin logs in and opens knowledge-base administration', async ({ page }) => {
  test.skip(!process.env.E2E_EMAIL || !process.env.E2E_PASSWORD, '需要通过本地环境变量提供测试账号')
  await page.goto('/')
  await page.getByLabel('邮箱').fill(process.env.E2E_EMAIL!)
  await page.getByLabel('密码').fill(process.env.E2E_PASSWORD!)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('heading', { name: '需要管理员处理的事项' })).toBeVisible()
  await page.getByRole('link', { name: '知识库管理' }).click()
  await expect(page.getByRole('heading', { name: '知识库' })).toBeVisible()
  await page.getByRole('link', { name: '服务设置' }).click()
  await expect(page.getByRole('heading', { name: '服务设置' })).toBeVisible()
})

test('HRBP asks a real policy question and receives a completed answer', async ({ page }) => {
  const accounts = roleAccounts()
  const account = accounts.hrbp
  test.skip(!account, 'E2E_ROLE_ACCOUNTS 缺少 hrbp 测试账号')
  // Streaming a real LLM answer token-by-token can exceed the 30s default.
  test.setTimeout(180_000)

  await login(page, account!)
  await page.getByRole('link', { name: '制度问答', exact: true }).click()
  await askPolicyQuestion(page)
})

test('employee asks a real policy question and sees evidence or the no-evidence next step', async ({ page }) => {
  const accounts = roleAccounts()
  const account = accounts.employee
  test.skip(!account, 'E2E_ROLE_ACCOUNTS 缺少 employee 测试账号')
  test.setTimeout(180_000)

  await login(page, account!)
  await page.getByRole('link', { name: '问 HR', exact: true }).click()
  await askPolicyQuestion(page)
})
