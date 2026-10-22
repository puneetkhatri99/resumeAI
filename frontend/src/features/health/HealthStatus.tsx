import { useEffect, useState } from 'react'
import { ApiError, apiGet } from '../../shared/api/client.ts'

/** Body of GET /api/health. Mirrors backend/app/features/health/schemas.py. */
type HealthResponse = {
  status: 'ok' | 'degraded'
  database: 'ok' | 'unavailable'
}

type HealthState = 'loading' | 'ok' | 'database_unavailable' | 'unreachable'

const STATUS_TEXT: Record<HealthState, string> = {
  loading: 'Checking backend…',
  ok: 'Backend OK · Database OK',
  database_unavailable: 'Backend OK · Database unavailable',
  unreachable: 'Backend unreachable',
}

/** Shows whether the backend API and its database are reachable. */
export function HealthStatus() {
  const [state, setState] = useState<HealthState>('loading')

  useEffect(() => {
    apiGet<HealthResponse>('/health')
      .then(() => setState('ok'))
      .catch((error: unknown) => {
        // The backend answers 503 when it is up but cannot reach Postgres.
        const isDatabaseDown = error instanceof ApiError && error.status === 503
        setState(isDatabaseDown ? 'database_unavailable' : 'unreachable')
      })
  }, [])

  return (
    <p role="status" className={`health-status health-${state}`}>
      {STATUS_TEXT[state]}
    </p>
  )
}
