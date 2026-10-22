# health (UI)

`HealthStatus` calls `GET /api/health` and shows one of these states: checking, backend + database OK, database unavailable (HTTP 503), or backend unreachable. It is the reference example of a frontend feature slice. The backend half is in `backend/app/features/health/`.

**Tests:**
- `HealthStatus.test.tsx` (Vitest, fetch mocked)
- `frontend/e2e/health.spec.ts` (Playwright, real backend)
