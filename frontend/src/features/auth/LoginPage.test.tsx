import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LoginPage } from './LoginPage.tsx'

// Synthetic account only.
const EMAIL = 'jane@example.com'
const PASSWORD = 'correct horse battery'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

async function submitLogin() {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Email'), EMAIL)
  await user.type(screen.getByLabelText('Password'), PASSWORD)
  await user.click(screen.getByRole('button', { name: 'Log in' }))
}

describe('LoginPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('labels its fields for password managers', () => {
    render(<LoginPage onLoggedIn={() => {}} />)

    expect(screen.getByLabelText('Email')).toHaveAttribute('autocomplete', 'username')
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'current-password')
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
  })

  it('sends the email and password as JSON and hands over the user', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({ email: EMAIL, role: 'recruiter' })))
    vi.stubGlobal('fetch', fetchMock)
    const onLoggedIn = vi.fn()
    render(<LoginPage onLoggedIn={onLoggedIn} />)

    await submitLogin()

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
    })
    expect(onLoggedIn).toHaveBeenCalledWith({ email: EMAIL, role: 'recruiter' })
  })

  it('shows "Logging in…" and disables the button while waiting', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})))
    render(<LoginPage onLoggedIn={() => {}} />)

    await submitLogin()

    expect(screen.getByRole('button', { name: 'Logging in…' })).toBeDisabled()
  })

  it.each([
    [401, 'Email or password is incorrect.'],
    [429, 'Too many requests. Try again in 840 seconds.'],
  ])('shows the backend message for %i', async (status, detail) => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ detail }, status))))
    const onLoggedIn = vi.fn()
    render(<LoginPage onLoggedIn={onLoggedIn} />)

    await submitLogin()

    expect(await screen.findByRole('alert')).toHaveTextContent(detail)
    expect(screen.getByRole('button', { name: 'Log in' })).toBeEnabled()
    expect(onLoggedIn).not.toHaveBeenCalled()
  })

  it('shows a network error', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))
    render(<LoginPage onLoggedIn={() => {}} />)

    await submitLogin()

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the server.')
  })
})
