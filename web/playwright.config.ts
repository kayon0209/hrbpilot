import { defineConfig, devices } from '@playwright/test'

// E2E needs a running backend + frontend. The defaults match the dev setup
// (uvicorn on 8001 via scripts, vite on 5173); override with E2E_API_TARGET /
// E2E_BASE_URL when running against different ports (see web/e2e/README.md).
export default defineConfig({
  testDir: './e2e',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  // The suite shares one local backend and performs real streaming requests.
  // Parallel Chromium workers exhaust Windows desktop resources and turn
  // navigation into ERR_INSUFFICIENT_RESOURCES rather than product evidence.
  workers: 1,
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  reporter: 'list',
})
