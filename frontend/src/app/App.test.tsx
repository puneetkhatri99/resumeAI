import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { UNAUTHORIZED_EVENT } from '../shared/api/client.ts'
import { App } from './App.tsx'

// Synthetic accounts only.
const RECRUITER = { email: 'jane@example.com', role: 'recruiter' }
const ADMIN = { email: 'admin@example.com', role: 'admin' }

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/**
 * fetch by URL: /api/auth/me answers `me` (a user, or 401 when null), login answers `loginAs`, logout 204, health
 * 200, and every other call never settles (the pages' own data isn't under test here).
 */
function stubBackend(me: object | null, loginAs: object = RECRUITER) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/auth/me') {
      return Promise.resolve(me ? jsonResponse(me) : jsonResponse({ detail: 'Not logged in.' }, 401))
    }
    if (url === '/api/auth/login') return Promise.resolve(jsonResponse(loginAs))
    if (url === '/api/auth/logout') return Promise.resolve(new Response(null, { status: 204 }))
    if (url === '/api/health') return Promise.resolve(jsonResponse({ status: 'ok', database: 'ok' }))
    return new Promise<Response>(() => {})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('App', () => {
  beforeEach(() => {
    window.scrollTo = vi.fn()
    window.location.hash = ''
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows the login page and no navigation when nobody is logged in', async () => {
    stubBackend(null)
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Log in' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })

  it('a recruiter sees the navigation without the Admin link', async () => {
    stubBackend(RECRUITER)
    render(<App />)

    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(nav).toHaveTextContent('Change password')
    expect(screen.getByRole('button', { name: 'Log out' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Admin' })).not.toBeInTheDocument()
  })

  it('an admin sees the Admin link', async () => {
    stubBackend(ADMIN)
    render(<App />)

    expect(await screen.findByRole('link', { name: 'Admin' })).toHaveAttribute('href', '#/admin')
  })

  it('the unauthorized event shows the login page', async () => {
    stubBackend(RECRUITER)
    render(<App />)
    await screen.findByRole('navigation', { name: 'Main' })

    act(() => {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    })

    expect(screen.getByRole('heading', { name: 'Log in' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })

  it('Log out shows the login page', async () => {
    stubBackend(RECRUITER)
    render(<App />)

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Log out' }))

    expect(await screen.findByRole('heading', { name: 'Log in' })).toBeInTheDocument()
  })

  it('#/ opens the candidate page, and the interview form lives at #/interview', async () => {
    stubBackend(RECRUITER)
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Candidates' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Candidates' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: /New interview/ })).toHaveAttribute('href', '#/interview')
    expect(screen.getByRole('link', { name: /Past interviews/ })).toHaveAttribute('href', '#/history')
    expect(screen.getByRole('link', { name: 'Add candidate' })).toHaveAttribute('href', '#/candidates/new')

    act(() => {
      window.location.hash = '#/interview'
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })

    expect(await screen.findByRole('heading', { name: 'Interview intake' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Interview' })).toHaveClass('is-current')
  })

  it('after login, the page the address asked for shows', async () => {
    window.location.hash = '#/password'
    stubBackend(null)
    render(<App />)
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('Email'), RECRUITER.email)
    await user.type(screen.getByLabelText('Password'), 'correct horse battery')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByRole('heading', { name: 'Change password' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Change password' })).toHaveAttribute('aria-current', 'page')
  })
})
