import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Recruiter } from './api.ts'
import { AdminPage } from './AdminPage.tsx'

// Synthetic accounts only.
const AMY: Recruiter = { id: 4, email: 'amy@example.com', created_at: '2026-11-03T09:15:00Z' }
const ZED: Recruiter = { id: 9, email: 'zed@example.com', created_at: '2026-11-03T10:00:00Z' }
const PASSWORD = 'correct horse battery'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** fetch answering the recruiter list first, then each of `answers` in order. */
function stubFetch(...answers: Response[]) {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(jsonResponse({ items: [AMY, ZED] }))
  for (const answer of answers) fetchMock.mockResolvedValueOnce(answer)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function rowOf(email: string): HTMLElement {
  return screen.getByRole('rowheader', { name: email }).closest('tr')!
}

describe('AdminPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('lists the recruiters with the day each was added', async () => {
    stubFetch()
    render(<AdminPage />)

    await screen.findByRole('rowheader', { name: AMY.email })
    const day = new Date(AMY.created_at).toLocaleDateString(undefined, { dateStyle: 'medium' })
    expect(within(rowOf(AMY.email)).getByText(day)).toBeInTheDocument()
    expect(screen.getAllByRole('rowheader').map((cell) => cell.textContent)).toEqual([AMY.email, ZED.email])
  })

  it('adds a recruiter in email order', async () => {
    const added: Recruiter = { id: 12, email: 'max@example.com', created_at: '2026-11-03T11:00:00Z' }
    const fetchMock = stubFetch(jsonResponse(added, 201))
    render(<AdminPage />)
    const user = userEvent.setup()
    await screen.findByRole('rowheader', { name: AMY.email })

    await user.type(screen.getByLabelText('Email'), added.email)
    await user.type(screen.getByLabelText('Password'), PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Add recruiter' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Added max@example.com.')
    expect(screen.getAllByRole('rowheader').map((cell) => cell.textContent)).toEqual([AMY.email, added.email, ZED.email])
    expect(fetchMock.mock.calls[1]).toEqual([
      '/api/auth/recruiters',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: added.email, password: PASSWORD }),
      },
    ])
  })

  it('shows the 409 detail when the email is taken', async () => {
    stubFetch(jsonResponse({ detail: 'An account with this email already exists.' }, 409))
    render(<AdminPage />)
    const user = userEvent.setup()
    await screen.findByRole('rowheader', { name: AMY.email })

    await user.type(screen.getByLabelText('Email'), AMY.email)
    await user.type(screen.getByLabelText('Password'), PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Add recruiter' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('An account with this email already exists.')
    expect(screen.getAllByRole('rowheader')).toHaveLength(2)
  })

  it('resets a password inline', async () => {
    const fetchMock = stubFetch(new Response(null, { status: 204 }))
    render(<AdminPage />)
    const user = userEvent.setup()
    await screen.findByRole('rowheader', { name: AMY.email })

    await user.click(screen.getByRole('button', { name: `Reset password for ${AMY.email}` }))
    await user.type(screen.getByLabelText(`New password for ${AMY.email}`), PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('status')).toHaveTextContent(`Password reset for ${AMY.email}.`)
    expect(fetchMock.mock.calls[1]).toEqual([
      `/api/auth/recruiters/${AMY.id}/password`,
      { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: PASSWORD }) },
    ])
    expect(screen.queryByLabelText(`New password for ${AMY.email}`)).not.toBeInTheDocument()
  })

  it('shows the 404 detail when the recruiter is gone', async () => {
    stubFetch(jsonResponse({ detail: 'Recruiter not found.' }, 404))
    render(<AdminPage />)
    const user = userEvent.setup()
    await screen.findByRole('rowheader', { name: AMY.email })

    await user.click(screen.getByRole('button', { name: `Reset password for ${AMY.email}` }))
    await user.type(screen.getByLabelText(`New password for ${AMY.email}`), PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Recruiter not found.')
  })

  it('asks before removing, then removes the row', async () => {
    const fetchMock = stubFetch(new Response(null, { status: 204 }))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<AdminPage />)
    await screen.findByRole('rowheader', { name: AMY.email })

    await userEvent.setup().click(screen.getByRole('button', { name: `Remove ${AMY.email}` }))

    expect(confirm).toHaveBeenCalledOnce()
    expect(await screen.findByRole('status')).toHaveTextContent(`Removed ${AMY.email}.`)
    expect(screen.queryByRole('rowheader', { name: AMY.email })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls[1]).toEqual([`/api/auth/recruiters/${AMY.id}`, { method: 'DELETE' }])
  })

  it('a cancelled confirm removes nothing', async () => {
    const fetchMock = stubFetch()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<AdminPage />)
    await screen.findByRole('rowheader', { name: AMY.email })

    await userEvent.setup().click(screen.getByRole('button', { name: `Remove ${AMY.email}` }))

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(screen.getByRole('rowheader', { name: AMY.email })).toBeInTheDocument()
  })

  it('shows "Admins only." on 403', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ detail: 'Admins only.' }, 403))))
    render(<AdminPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Admins only.')
    expect(screen.queryByRole('button', { name: 'Add recruiter' })).not.toBeInTheDocument()
  })
})
