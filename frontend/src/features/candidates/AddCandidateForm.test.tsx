import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AddCandidateForm } from './AddCandidateForm.tsx'
import type { CandidateEvent } from './api.ts'

const NEW_RESUME_URL = 'https://storage.example.com/resumes/candidates/abc/resume.pdf'
const NETWORK_ERROR_MESSAGE = 'Could not reach the server. Check your connection and try again.'

/** One Server-Sent Event as the backend writes it: every CandidateEvent field, the unused ones null. */
function sseEvent(event: Partial<CandidateEvent>): string {
  return `data: ${JSON.stringify({ new_resume_url: null, status: null, detail: null, ...event })}\n\n`
}

/** A 200 event-stream response whose body is these chunks. */
function eventStreamResponse(...chunks: string[]): Response {
  return new Response(chunks.join(''), { headers: { 'Content-Type': 'text/event-stream' } })
}

/** A 200 event-stream response whose body chunks the test sends one at a time, then closes. */
function controlledEventStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({
    start(streamController) {
      controller = streamController
    },
  })
  return {
    response: new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }),
    send: (text: string) => controller.enqueue(new TextEncoder().encode(text)),
    close: () => controller.close(),
  }
}

/**
 * Submits the form directly: jsdom ignores files set by user.upload (in FormData and the "required" check), so
 * the form sends an empty file field, which is enough to drive the stream.
 */
function submit() {
  fireEvent.submit(screen.getByRole('button', { name: 'Start' }).closest('form')!)
}

const ERROR_CASES = [
  {
    scenario: 'the AI spending cap is reached (402)',
    fetchImpl: () => Promise.resolve(Response.json({ detail: 'AI spending limit reached. Uploads are paused.' }, { status: 402 })),
    expectedText: 'AI spending limit reached. Uploads are paused.',
  },
  {
    scenario: 'the request is over 10 MB (413 with a plain-text body)',
    fetchImpl: () => Promise.resolve(new Response('Request Entity Too Large', { status: 413 })),
    expectedText: 'The CV is larger than 10 MB.',
  },
  {
    scenario: 'the rate limit is hit (429)',
    fetchImpl: () => Promise.resolve(Response.json({ detail: 'Too many requests. Try again in 60 seconds.' }, { status: 429 })),
    expectedText: 'Too many requests. Try again in 60 seconds.',
  },
  {
    scenario: 'the backend is unreachable',
    fetchImpl: () => Promise.reject(new TypeError('Failed to fetch')),
    expectedText: NETWORK_ERROR_MESSAGE,
  },
  {
    scenario: 'storing the files fails (a 502 error event)',
    fetchImpl: () =>
      Promise.resolve(
        eventStreamResponse(
          sseEvent({ stage: 'reading' }),
          sseEvent({ stage: 'uploading' }),
          sseEvent({ stage: 'error', status: 502, detail: 'Could not store the resume files. Try again.' }),
        ),
      ),
    expectedText: 'Could not store the resume files. Try again.',
  },
  {
    scenario: 'the stream ends without a result',
    fetchImpl: () => Promise.resolve(eventStreamResponse(sseEvent({ stage: 'reading' }))),
    expectedText: NETWORK_ERROR_MESSAGE,
  },
]

describe('AddCandidateForm', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('sends the CV, disables the form while sending, shows each stage, then links to the new resume', async () => {
    const stream = controlledEventStream()
    const fetchMock = vi.fn<typeof fetch>(() => Promise.resolve(stream.response))
    vi.stubGlobal('fetch', fetchMock)
    render(<AddCandidateForm />)

    submit()

    expect(screen.getByRole('button', { name: 'Adding…' })).toBeDisabled()
    expect(screen.getByLabelText('CV')).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('Extracting text…')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/candidates')
    expect(init?.method).toBe('POST')
    const body = init?.body as FormData
    expect(body.get('file')).toBeInstanceOf(File)

    // Each stage is held on screen for MIN_STAGE_MS (700 ms), so later stages show up to a few seconds later.
    stream.send(sseEvent({ stage: 'reading' }) + sseEvent({ stage: 'extracting' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('AI cleaning…'), { timeout: 3000 })
    stream.send(sseEvent({ stage: 'generating' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Creating resume…'), { timeout: 3000 })
    stream.send(sseEvent({ stage: 'uploading' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Uploading…'), { timeout: 3000 })
    stream.send(sseEvent({ stage: 'done', new_resume_url: NEW_RESUME_URL }))
    stream.close()

    const link = await screen.findByRole('link', { name: 'Open new resume' }, { timeout: 3000 })
    expect(link).toHaveAttribute('href', NEW_RESUME_URL)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByRole('link', { name: 'View candidates' })).toHaveAttribute('href', '#/candidates')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  }, 10_000)

  it.each(ERROR_CASES)('shows "$expectedText" when $scenario', async ({ fetchImpl, expectedText }) => {
    vi.stubGlobal('fetch', vi.fn(fetchImpl))
    render(<AddCandidateForm />)

    submit()

    expect(await screen.findByRole('alert')).toHaveTextContent(expectedText)
    expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Open new resume' })).not.toBeInTheDocument()
  })
})
