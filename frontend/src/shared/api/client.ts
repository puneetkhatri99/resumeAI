/** Prefix for every backend call. In dev, Vite proxies it to FastAPI (see vite.config.ts). */
const API_BASE_PATH = '/api'

/** Thrown when the backend answers with a non-2xx status. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * The `window` event every helper here dispatches when the backend answers 401 ("Not logged in."), so `App` can
 * show the login page whichever call found out. No endpoint uses 401 for anything else.
 */
export const UNAUTHORIZED_EVENT = 'resumeai:unauthorized'

/**
 * The ApiError for a non-2xx response, with the backend's `detail` as its message when that is a string (FastAPI's
 * HTTPException shape), otherwise a generic one. Dispatches UNAUTHORIZED_EVENT first on a 401.
 */
async function toApiError(response: Response, method: string, path: string): Promise<ApiError> {
  if (response.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
  // Not every error body is JSON: the 413 from the request-size limit is plain text.
  const errorBody: unknown = await response.json().catch(() => null)
  const detail = (errorBody as { detail?: unknown } | null)?.detail
  return new ApiError(
    response.status,
    typeof detail === 'string' ? detail : `${method} ${API_BASE_PATH}${path} failed with status ${response.status}`,
  )
}

/**
 * GET `path` (relative to /api) and return the parsed JSON body.
 * Throws ApiError on a non-2xx status (dispatching UNAUTHORIZED_EVENT on 401). A network failure rejects with the
 * browser's TypeError.
 */
export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_PATH}${path}`)
  if (!response.ok) throw await toApiError(response, 'GET', path)
  return (await response.json()) as T
}

/**
 * Send a POST, PUT or DELETE to `path` (relative to /api), with `jsonBody` as JSON when given. Resolves to the
 * parsed JSON body, or `undefined` on 204. Throws ApiError on a non-2xx status (dispatching UNAUTHORIZED_EVENT on
 * 401). A network failure rejects with the browser's TypeError.
 */
export async function apiRequest<T = undefined>(method: 'POST' | 'PUT' | 'DELETE', path: string, jsonBody?: unknown): Promise<T> {
  const init: RequestInit =
    jsonBody === undefined
      ? { method }
      : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(jsonBody) }
  const response = await fetch(`${API_BASE_PATH}${path}`, init)
  if (!response.ok) throw await toApiError(response, method, path)
  return (response.status === 204 ? undefined : await response.json()) as T
}

/**
 * POST `body` as multipart/form-data to `path` (relative to /api) and read the answer as a Server-Sent Events
 * stream: `onEvent` gets each event's JSON `data`, in order, and the promise resolves when the stream ends.
 * (`EventSource` can only GET, hence fetch.) Throws ApiError on a non-2xx status (dispatching UNAUTHORIZED_EVENT on
 * 401). Its message is the backend's `detail` when that is a string (FastAPI's HTTPException shape), otherwise a
 * generic one. An error thrown by `onEvent` rejects the promise. A network failure rejects with the browser's
 * TypeError.
 */
export async function apiPostEventStream<T>(path: string, body: FormData, onEvent: (event: T) => void): Promise<void> {
  // No Content-Type header: the browser sets multipart/form-data with its boundary itself.
  const response = await fetch(`${API_BASE_PATH}${path}`, { method: 'POST', body })
  if (!response.ok) throw await toApiError(response, 'POST', path)

  const reader = response.body!.pipeThrough(new TextDecoderStream()).getReader()
  // An event can arrive split across chunks (the final one carries the whole CV text), so the unfinished tail
  // waits here for the rest. Events end with a blank line.
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) return
    const events = (buffer + value).split('\n\n')
    buffer = events.pop() ?? ''
    for (const line of events.flatMap((event) => event.split('\n'))) {
      // Other lines, such as the ": ping" keepalive comments, carry no data.
      if (line.startsWith('data:')) onEvent(JSON.parse(line.slice('data:'.length)) as T)
    }
  }
}
