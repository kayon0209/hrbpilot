import { expect, test } from '@playwright/test'

type RoleAccount = { email: string; password: string }

function roleAccounts() {
  const raw = process.env.E2E_ROLE_ACCOUNTS
  return raw ? JSON.parse(raw) as Partial<Record<'employee' | 'hrbp' | 'hr_manager' | 'admin', RoleAccount>> : {}
}

const VIEWPORTS = [
  { name: 'desktop-1440', width: 1440, height: 900 },
  { name: 'laptop-1024', width: 1024, height: 768 },
  { name: 'tablet-768', width: 768, height: 1024 },
  { name: 'mobile-375', width: 375, height: 812 },
]

// 每个角色登录后重点检查的页面（与指令清单对齐）
const ROLE_PAGES: Partial<Record<'employee' | 'hrbp' | 'hr_manager' | 'admin', string[]>> = {
  employee: ['/policy', '/my-requests'],
  hrbp: ['/', '/tasks', '/policy', '/interview', '/culture', '/weekly'],
  hr_manager: ['/', '/tasks', '/team', '/knowledge-feedback', '/cases'],
  admin: ['/admin', '/admin/users', '/admin/audit', '/admin/knowledge-bases', '/admin/settings'],
}

async function login(page: import('@playwright/test').Page, account: RoleAccount) {
  await page.goto('/')
  await page.getByLabel('邮箱').fill(account.email)
  await page.getByLabel('密码').fill(account.password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.waitForURL(/\/(admin)?/, { waitUntil: 'domcontentloaded' })
}

for (const viewport of VIEWPORTS) {
  for (const [role, pages] of Object.entries(ROLE_PAGES) as [keyof typeof ROLE_PAGES, string[]][]) {
    test(`${role} pages have no overflow or console errors at ${viewport.name}`, async ({ page, browser }) => {
      const account = roleAccounts()[role]
      test.skip(!account, `E2E_ROLE_ACCOUNTS 缺少 ${role} 测试账号`)

      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } })
      const ctxPage = await context.newPage()
      const consoleErrors: string[] = []
      ctxPage.on('console', msg => {
        if (msg.type() === 'error') consoleErrors.push(msg.text())
      })

      await login(ctxPage, account!)
      for (const path of pages!) {
        const response = await ctxPage.goto(path, { waitUntil: 'domcontentloaded' })
        // 404/403/500 都是坏旅程；等待渲染稳定后再量宽度。
        expect(response?.status(), `${role} ${path} HTTP status`).toBeLessThan(400)
        await ctxPage.waitForTimeout(400)
        const overflow = await ctxPage.evaluate(() => {
          if (document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1) return 0
          return document.documentElement.scrollWidth - document.documentElement.clientWidth
        })
        expect(overflow, `${role} ${path} 水平溢出 ${viewport.name}`).toBe(0)
      }

      // React key 冲突与渲染错误都会以 console.error 形式出现。
      expect(consoleErrors, `${role} ${viewport.name} console errors`).toEqual([])
      await context.close()
      await page.close()
    })
  }
}
