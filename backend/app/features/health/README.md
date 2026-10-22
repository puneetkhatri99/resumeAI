# health

**Purpose:** reports whether the API is running and can reach Postgres. It is also the reference example of a feature slice.

## API
| Method | Path | Response |
|---|---|---|
| GET | `/api/health` | `200 {"status": "ok", "database": "ok"}`, or `503 {"status": "degraded", "database": "unavailable"}` |

## Files
- `router.py`: the endpoint. It runs `SELECT 1` through the `get_db` session.
- `schemas.py`: `HealthResponse`.

## Data
No tables.

## Tests
`backend/tests/features/health/test_router.py`
