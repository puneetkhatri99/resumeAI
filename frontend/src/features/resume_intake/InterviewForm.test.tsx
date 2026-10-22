import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { IntakeEvent, ResumeIntake } from './api.ts'
import { InterviewForm } from './InterviewForm.tsx'

// The form values as the browser sends them (every multipart value is a string).
const ANSWERS = {
  notice_period: 'One month',
  salary_expectation: '$60k/year',
  work_mode: 'onsite',
  rating: '4',
  strengths: 'Clear communicator',
  concerns: 'Little cloud experience',
  overall_notes: 'Invite to the second round',
}

const RESULT: ResumeIntake = {
  ...ANSWERS,
  id: 1,
  original_filename: 'cv.txt',
  file_type: 'txt',
  extracted_text: 'Jane Doe\nPython developer',
  structured_data: null,
  work_mode: 'onsite',
  rating: 4,
  created_at: '2026-10-22T10:00:00Z',
}
const NETWORK_ERROR_MESSAGE = 'Could not reach the server. Check your connection and try again.'
// The visible label of each work mode's radio.
const WORK_MODE_LABELS: Record<string, string> = { remote: 'Remote', hybrid: 'Hybrid', onsite: 'On-site' }

/** One Server-Sent Event as the backend writes it: every IntakeEvent field, the unused ones null. */
function sseEvent(event: Partial<IntakeEvent>): string {
  return `data: ${JSON.stringify({ result: null, status: null, detail: null, ...event })}\n\n`
}

/** A 200 event-stream response whose body is these chunks. */
function eventStreamResponse(...chunks: string[]): Response {
  return new Response(chunks.join(''), { headers: { 'Content-Type': 'text/event-stream' } })
}

/** A 200 event-stream response whose body chunks the test sends one at a time, then closes. */
function controlledEventStream() {
  // `start` runs inside the constructor, so the controller is set before the stream is returned.
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
 * Fills every answer (`answers` overrides the defaults) and submits. jsdom has no DataTransfer and ignores
 * files set by user.upload (in both FormData and the "required" check), so no CV can be chosen here, a button
 * click would be blocked, and the 10 MB CV check can't be reached either.
 * The form is submitted directly instead; e2e/resume_intake.spec.ts covers the real CV and button in a browser.
 */
async function fillAndSubmit(user: UserEvent, answers = ANSWERS) {
  await user.type(screen.getByLabelText('Notice period / start date'), answers.notice_period)
  await user.type(screen.getByLabelText('Salary expectation'), answers.salary_expectation)
  await user.click(screen.getByRole('radio', { name: WORK_MODE_LABELS[answers.work_mode] }))
  await user.click(screen.getByRole('radio', { name: answers.rating }))
  await user.type(screen.getByLabelText('Strengths'), answers.strengths)
  await user.type(screen.getByLabelText('Concerns'), answers.concerns)
  await user.type(screen.getByLabelText('Overall notes'), answers.overall_notes)
  fireEvent.submit(screen.getByRole('button', { name: 'Submit' }).closest('form')!)
}

const ERROR_CASES = [
  {
    scenario: 'the backend rejects the CV (422 with a detail message)',
    fetchImpl: () => Promise.resolve(Response.json({ detail: 'The CV has more than 4 pages.' }, { status: 422 })),
    expectedText: 'The CV has more than 4 pages.',
  },
  {
    scenario: 'the request is over 10 MB (413 with a plain-text body)',
    fetchImpl: () => Promise.resolve(new Response('Request Entity Too Large', { status: 413 })),
    expectedText: 'The CV is larger than 10 MB.',
  },
  {
    scenario: 'an answer fails validation (422 with a detail list)',
    fetchImpl: () =>
      Promise.resolve(Response.json({ detail: [{ loc: ['body', 'strengths'], msg: 'too long' }] }, { status: 422 })),
    expectedText: 'POST /api/resume_intake failed with status 422',
  },
  {
    scenario: 'the backend is unreachable',
    fetchImpl: () => Promise.reject(new TypeError('Failed to fetch')),
    expectedText: NETWORK_ERROR_MESSAGE,
  },
  {
    scenario: 'the AI finds no name or email (a 422 error event)',
    fetchImpl: () =>
      Promise.resolve(
        eventStreamResponse(
          sseEvent({ stage: 'reading' }),
          sseEvent({ stage: 'extracting' }),
          sseEvent({ stage: 'error', status: 422, detail: "Couldn't find the candidate's name or email in the CV." }),
        ),
      ),
    expectedText: "Couldn't find the candidate's name or email in the CV.",
  },
  {
    scenario: 'the AI call fails (a 502 error event)',
    fetchImpl: () =>
      Promise.resolve(
        eventStreamResponse(
          sseEvent({ stage: 'reading' }),
          sseEvent({ stage: 'extracting' }),
          sseEvent({ stage: 'error', status: 502, detail: 'AI extraction failed, please try again.' }),
        ),
      ),
    expectedText: 'AI extraction failed, please try again.',
  },
  {
    scenario: 'the stream ends without a result',
    fetchImpl: () => Promise.resolve(eventStreamResponse(sseEvent({ stage: 'reading' }))),
    expectedText: NETWORK_ERROR_MESSAGE,
  },
]

describe('InterviewForm', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('starts work mode and rating with nothing chosen, which blocks the submit until each is chosen', async () => {
    const user = userEvent.setup()
    render(<InterviewForm onSubmitted={vi.fn()} />)

    for (const [group, choice] of [
      [/^Work mode/, 'Hybrid'],
      [/^Rating/, '5'],
    ]) {
      const radios = within(screen.getByRole('group', { name: group })).getAllByRole('radio')
      expect(radios.filter((radio) => (radio as HTMLInputElement).checked)).toHaveLength(0)
      expect(radios[0]).toBeInvalid()
      await user.click(screen.getByRole('radio', { name: choice }))
      expect(radios[0]).toBeValid()
    }
  })

  it('sends every answer and the file field, shows "Submitting…", then calls onSubmitted after each stage has been shown', async () => {
    let respond: (response: Response) => void = () => {}
    const fetchMock = vi.fn<typeof fetch>(() => new Promise((resolve) => (respond = resolve)))
    vi.stubGlobal('fetch', fetchMock)
    const onSubmitted = vi.fn()
    render(<InterviewForm onSubmitted={onSubmitted} />)

    await fillAndSubmit(userEvent.setup())

    expect(screen.getByRole('button', { name: 'Submitting…' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('Submitting…')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/resume_intake')
    expect(init?.method).toBe('POST')
    const body = init?.body as FormData
    expect(Object.fromEntries([...body.entries()].filter(([name]) => name !== 'file'))).toEqual(ANSWERS)
    expect(body.get('file')).toBeInstanceOf(File)

    respond(
      eventStreamResponse(
        sseEvent({ stage: 'reading' }),
        sseEvent({ stage: 'extracting' }),
        sseEvent({ stage: 'done', result: RESULT }),
      ),
    )
    // Reading and extracting are each held on screen for MIN_STAGE_MS (700 ms) before the result is passed on.
    await waitFor(() => expect(onSubmitted).toHaveBeenCalledWith(RESULT), { timeout: 3000 })
  })

  it('shows each stage as its event streams in, and reads a "done" event split across chunks', async () => {
    const stream = controlledEventStream()
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(stream.response)))
    const onSubmitted = vi.fn()
    render(<InterviewForm onSubmitted={onSubmitted} />)
    await fillAndSubmit(userEvent.setup())

    stream.send(sseEvent({ stage: 'reading' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Extracting text…'))

    // A keepalive comment, then the first part of the "done" event (it carries the whole CV text).
    const done = sseEvent({ stage: 'done', result: RESULT })
    stream.send(sseEvent({ stage: 'extracting' }) + ': ping\n\n' + done.slice(0, 40))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('AI cleaning…'))
    expect(onSubmitted).not.toHaveBeenCalled()

    stream.send(done.slice(40))
    stream.close()
    await waitFor(() => expect(onSubmitted).toHaveBeenCalledWith(RESULT))
  })

  it('refuses an answer of only spaces with "Fill in <Label>.", focuses it and sends nothing', async () => {
    const fetchMock = vi.fn<typeof fetch>()
    vi.stubGlobal('fetch', fetchMock)
    render(<InterviewForm onSubmitted={vi.fn()} />)

    await fillAndSubmit(userEvent.setup(), { ...ANSWERS, strengths: '   ' })

    expect(screen.getByRole('alert')).toHaveTextContent('Fill in Strengths.')
    expect(screen.getByLabelText('Strengths')).toHaveFocus()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each(ERROR_CASES)('shows "$expectedText" when $scenario', async ({ fetchImpl, expectedText }) => {
    vi.stubGlobal('fetch', vi.fn(fetchImpl))
    const onSubmitted = vi.fn()
    render(<InterviewForm onSubmitted={onSubmitted} />)

    await fillAndSubmit(userEvent.setup())

    expect(await screen.findByRole('alert')).toHaveTextContent(expectedText)
    expect(screen.getByRole('button', { name: 'Submit' })).toBeEnabled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(onSubmitted).not.toHaveBeenCalled()
  })
})
