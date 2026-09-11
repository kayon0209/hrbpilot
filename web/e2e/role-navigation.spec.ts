import { expect, test } from '@playwright/test'

type RoleAccount = {
  email: string
  password: string
}

type RoleCase = {
  role: 'employee' | 'hrbp' | 'hr_manager' | 'admin'
  homePath: string
  heading: string
  visibleNavigation: string[]
  hiddenNavigation: string[]
}

const cases: RoleCase[] = [
  {
    role: 'employee',
    homePath: '/policy',
    heading: '制度问答',
    visibleNavigation: ['问 HR', '我的请求'],
    hiddenNavigation: ['团队待处理', '系统状态'],
  },
  {
    role: 'hrbp',
    homePath: '/',
    heading: '先做最重要的事',
    visibleNavigation: ['今日工作', '工作任务', '制度问答'],
    hiddenNavigation: ['团队待处理', '系统状态'],
  },
  {
    role: 'hr_manager',
    homePath: '/',
    heading: '先做最重要的事',
    visibleNavigation: ['今日工作', '团队待处理', '知识与反馈'],
    hiddenNavigation: ['知识库管理', '系统状态'],
  },
  {
    role: 'admin',
    homePath: '/admin',
    heading: '需要管理员处理的事项',
    visibleNavigation: ['系统状态', '用户与权限', '审计记录'],
    hiddenNavigation: ['今日工作', '制度问答'],
  },
]

const rawAccounts = process.env.E2E_ROLE_ACCOUNTS
const accounts = rawAccounts ? (JSON.parse(rawAccounts) as Partial<Record<RoleCase['role'], RoleAccount>>) : {}

for (const roleCase of cases) {
  test(`${roleCase.role} lands on the correct workspace with scoped navigation`, async ({ page }, testInfo) => {
    const account = accounts[roleCase.role]
    test.skip(!account, `E2E_ROLE_ACCOUNTS 缺少 ${roleCase.role} 测试账号`)

    await page.goto('/')
    await page.getByLabel('邮箱').fill(account!.email)
    await page.getByLabel('密码').fill(account!.password)
    await page.getByRole('button', { name: '登录' }).click()

    await expect(page).toHaveURL(new RegExp(`${roleCase.homePath === '/' ? '/$' : `${roleCase.homePath}$`}`))
    await expect(page.getByRole('heading', { name: roleCase.heading })).toBeVisible()

    for (const label of roleCase.visibleNavigation) {
      await expect(page.getByRole('link', { name: label, exact: true })).toBeVisible()
    }
    for (const label of roleCase.hiddenNavigation) {
      await expect(page.getByRole('link', { name: label, exact: true })).toHaveCount(0)
    }

    await page.screenshot({ path: testInfo.outputPath(`${roleCase.role}-home.png`), fullPage: true })
  })
}
