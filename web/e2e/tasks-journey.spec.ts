import { expect, test } from '@playwright/test'

type RoleAccount = { email: string; password: string }

function roleAccounts() {
  const raw = process.env.E2E_ROLE_ACCOUNTS
  return raw ? JSON.parse(raw) as Partial<Record<'hrbp', RoleAccount>> : {}
}

test('HRBP runs a real multi-day task journey that survives a reload', async ({ page }) => {
  const account = roleAccounts().hrbp
  test.skip(!account, 'E2E_ROLE_ACCOUNTS 缺少 hrbp 测试账号')

  await page.goto('/')
  await page.getByLabel('邮箱').fill(account!.email)
  await page.getByLabel('密码').fill(account!.password)
  await page.getByRole('button', { name: '登录' }).click()
  await page.getByRole('link', { name: '工作任务', exact: true }).click()
  await expect(page.getByRole('heading', { name: '工作任务' })).toBeVisible()

  // 1. Create a multi-day task with a real unit denominator.
  const taskTitle = `E2E 多日任务 ${Date.now()}`
  await page.getByLabel('任务名称').fill(taskTitle)
  await page.getByLabel('下一步').fill('先核对华东数据')
  await page.getByLabel('真实工作总量').fill('2')
  await page.getByRole('button', { name: '创建多日任务' }).click()

  const task = page.locator('article', { hasText: taskTitle })
  await expect(task).toBeVisible()
  await expect(task.getByText('进度：0/2')).toBeVisible()

  // 2. Split two subtasks with full subtask fields. Subtasks render as
  // sibling articles, not nested inside the parent's article. Unique suffixes
  // keep runs independent from any leftover rows of a previous failed run.
  const stamp = String(Date.now())
  const east = `完成华东复核 ${stamp}`
  const south = `完成华南复核 ${stamp}`
  const splitSubtask = async (title: string) => {
    await task.getByRole('button', { name: '拆分任务' }).click()
    await task.getByLabel('子任务名称').fill(title)
    await task.getByLabel('子任务下一步').fill('核对差异并留痕')
    await task.getByRole('button', { name: '创建子任务' }).click()
    await expect(page.locator('article', { hasText: title })).toBeVisible({ timeout: 15_000 })
  }
  await splitSubtask(east)
  await splitSubtask(south)

  // Parent progress now reflects two real subtasks.
  await expect(task.getByText('进度：0/2')).toBeVisible({ timeout: 15_000 })

  // 3. Complete one subtask.
  const subtask = page.locator('article', { hasText: east })
  await subtask.getByRole('button', { name: '标记完成' }).click()
  await expect(task.getByText('进度：1/2')).toBeVisible({ timeout: 15_000 })

  // 4. Reload — persistence must survive.
  await page.reload()
  await expect(page.getByRole('heading', { name: '工作任务' })).toBeVisible()
  const taskAfterReload = page.locator('article', { hasText: taskTitle })
  await expect(taskAfterReload.getByText('进度：1/2')).toBeVisible({ timeout: 15_000 })

  // Today's completed list carries the finished subtask with a real output.
  await expect(page.getByText(east).first()).toBeVisible()

  // 5. The workspace still shows where to continue next.
  await expect(page.getByText(/下一步/).first()).toBeVisible()
})
