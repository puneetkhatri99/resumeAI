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
 * GET `path` (relative to /api) and return the parsed JSON body.
 * Throws ApiError on a non-2xx status. A network failure rejects with the browser's TypeError.
 */
export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_PATH}${path}`)
  if (!response.ok) {
    throw new ApiError(response.status, `GET ${API_BASE_PATH}${path} failed with status ${response.status}`)
  }
  return (await response.json()) as T
}

/**
 * POST `body` as multipart/form-data to `path` (relative to /api) and read the answer as a Server-Sent Events
 * stream: `onEvent` gets each event's JSON `data`, in order, and the promise resolves when the stream ends.
 * (`EventSource` can only GET, hence fetch.) Throws ApiError on a non-2xx status. Its message is the backend's
 * `detail` when that is a string (FastAPI's HTTPException shape), otherwise a generic one. An error thrown by
 * `onEvent` rejects the promise. A network failure rejects with the browser's TypeError.
 */
export async function apiPostEventStream<T>(path: string, body: FormData, onEvent: (event: T) => void): Promise<void> {
  // No Content-Type header: the browser sets multipart/form-data with its boundary itself.
  const response = await fetch(`${API_BASE_PATH}${path}`, { method: 'POST', body })
  if (!response.ok) {
    // Not every error body is JSON: the 413 from the request-size limit is plain text.
    const errorBody: unknown = await response.json().catch(() => null)
    const detail = (errorBody as { detail?: unknown } | null)?.detail
    throw new ApiError(
      response.status,
      typeof detail === 'string' ? detail : `POST ${API_BASE_PATH}${path} failed with status ${response.status}`,
    )
  }

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
