/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Dev only: forward API calls to FastAPI so the browser sees one origin (no CORS needed).
    proxy: { '/api': 'http://localhost:8000' },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/shared/test/setup.ts'],
    // Only unit/component tests; Playwright owns e2e/.
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
