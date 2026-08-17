import { defineConfig, devices } from '@playwright/test'
export default defineConfig({ testDir: './e2e', use: { baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3000', trace: 'retain-on-failure' }, projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }], reporter: 'list' })
