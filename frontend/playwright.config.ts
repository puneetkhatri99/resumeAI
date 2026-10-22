import { defineConfig, devices } from '@playwright/test'

// Playwright runs webServer commands through cmd.exe on Windows, which needs backslash paths.
const backendPython =
  process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python'

/**
 * End-to-end tests against the real stack. Starts the FastAPI backend (needs Postgres and
 * backend/.env) and the Vite dev server, or reuses them if they are already running.
 */
export default defineConfig({
  testDir: './e2e',
  use: { baseURL: 'http://localhost:5173' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `${backendPython} -m uvicorn app.main:app --port 8000`,
      cwd: '../backend',
      // openapi.json answers 200 even when the DB is down, so the readiness check measures the API alone.
      url: 'http://localhost:8000/openapi.json',
      reuseExistingServer: true,
    },
    {
      command: 'npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: true,
    },
  ],
})
