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
  await page.goto('/')
  await page.getByLabel('邮箱').fill(process.env.E2E_EMAIL!)
  await page.getByLabel('密码').fill(process.env.E2E_PASSWORD!)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('heading', { name: '概览' })).toBeVisible()
  await page.getByRole('link', { name: '知识库' }).click()
  await expect(page.getByRole('heading', { name: '知识库' })).toBeVisible()
  await page.getByRole('link', { name: '制度问答' }).click()
  await page.getByLabel('问题').fill('请假超过三天如何审批？')
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.getByText('依据与来源')).toBeVisible({ timeout: 30_000 })
})
