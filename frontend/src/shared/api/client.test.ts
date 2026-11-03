import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiGet, apiPostEventStream, apiRequest, UNAUTHORIZED_EVENT } from './client.ts'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

// Listener removals run after each test.
const afterEachCleanups: (() => void)[] = []

/** Counts UNAUTHORIZED_EVENT dispatches until the test ends. */
function countUnauthorizedEvents(): { count: number } {
  const counter = { count: 0 }
  const listener = () => counter.count++
  window.addEventListener(UNAUTHORIZED_EVENT, listener)
  afterEachCleanups.push(() => window.removeEventListener(UNAUTHORIZED_EVENT, listener))
  return counter
}

// Each helper called the way a failing request reaches it.
const HELPERS: [string, () => Promise<unknown>][] = [
  ['apiGet', () => apiGet('/auth/me')],
  ['apiRequest', () => apiRequest('POST', '/auth/password', { current_password: 'x' })],
  ['apiPostEventStream', () => apiPostEventStream('/candidates', new FormData(), () => {})],
]

describe('client', () => {
  afterEach(() => {
    afterEachCleanups.splice(0).forEach((cleanup) => cleanup())
    vi.unstubAllGlobals()
  })

  it.each(HELPERS)('%s dispatches the unauthorized event on 401 and throws the detail', async (_name, call) => {
    const events = countUnauthorizedEvents()
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ detail: 'Not logged in.' }, 401))))

    const error = await call().catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 401, message: 'Not logged in.' })
    expect(events.count).toBe(1)
  })

  it.each(HELPERS)('%s does not dispatch it on other errors', async (_name, call) => {
    const events = countUnauthorizedEvents()
    for (const status of [400, 403, 404, 429, 503]) {
      vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ detail: 'No.' }, status))))
      await expect(call()).rejects.toMatchObject({ status })
    }
    expect(events.count).toBe(0)
  })

  it('apiRequest sends a JSON body and resolves to the parsed answer', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({ id: 3 }, 201)))
    vi.stubGlobal('fetch', fetchMock)

    await expect(apiRequest('POST', '/auth/recruiters', { email: 'a@example.com' })).resolves.toEqual({ id: 3 })
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/recruiters', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'a@example.com' }),
    })
  })

  it.each(['PUT', 'DELETE'] as const)('apiRequest sends %s and resolves to undefined on 204', async (method) => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(null, { status: 204 })))
    vi.stubGlobal('fetch', fetchMock)

    await expect(apiRequest(method, '/auth/recruiters/7')).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/recruiters/7', { method })
  })

  it('apiRequest falls back to a generic message when the error body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('Payload Too Large', { status: 413 }))))

    await expect(apiRequest('DELETE', '/candidates/7')).rejects.toMatchObject({
      status: 413,
      message: 'DELETE /api/candidates/7 failed with status 413',
    })
  })
})
