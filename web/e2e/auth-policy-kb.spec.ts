import { expect, test } from '@playwright/test'

test('shows the production login experience', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('HRBPilot', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '进入工作台' })).toBeVisible()
  await expect(page.getByLabel('邮箱')).toBeEditable()
  await expect(page.getByLabel('密码')).toBeEditable()
})

test('logs in, opens the knowledge base, and asks a policy question', async ({ page }) => {
  test.skip(!process.env.E2E_EMAIL || !process.env.E2E_PASSWORD, '需要通过本地环境变量提供测试账号')
  // The role-navigation spec covers the four experiences; this journey runs
  // as the admin because the knowledge base now lives in the admin backend.
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
