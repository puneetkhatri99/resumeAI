import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { UNAUTHORIZED_EVENT } from '../../shared/api/client.ts'
import { ChangePasswordForm } from './ChangePasswordForm.tsx'

const CURRENT = 'correct horse battery'
const NEW = 'a brand new password'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

async function fillAndSubmit(repeat = NEW) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Current password'), CURRENT)
  await user.type(screen.getByLabelText('New password'), NEW)
  await user.type(screen.getByLabelText('Repeat new password'), repeat)
  await user.click(screen.getByRole('button', { name: 'Change password' }))
}

describe('ChangePasswordForm', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('asks for at least 12 characters in the browser', () => {
    render(<ChangePasswordForm />)

    expect(screen.getByLabelText('New password')).toHaveAttribute('minlength', '12')
    expect(screen.getByLabelText('Repeat new password')).toHaveAttribute('minlength', '12')
    expect(screen.getByLabelText('New password')).toHaveAttribute('autocomplete', 'new-password')
  })

  it('blocks a mismatched repeat without a request', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    render(<ChangePasswordForm />)

    await fillAndSubmit('a different password')

    expect(await screen.findByRole('alert')).toHaveTextContent('The new passwords don’t match.')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('sends both passwords and says "Password changed."', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(null, { status: 204 })))
    vi.stubGlobal('fetch', fetchMock)
    render(<ChangePasswordForm />)

    await fillAndSubmit()

    expect(await screen.findByRole('status')).toHaveTextContent('Password changed.')
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: CURRENT, new_password: NEW }),
    })
    expect(screen.getByLabelText('Current password')).toHaveValue('')
  })

  it('shows a wrong current password (400) and stays logged in', async () => {
    const onUnauthorized = vi.fn()
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ detail: 'Current password is incorrect.' }, 400))))
    render(<ChangePasswordForm />)

    await fillAndSubmit()

    expect(await screen.findByRole('alert')).toHaveTextContent('Current password is incorrect.')
    expect(onUnauthorized).not.toHaveBeenCalled()
    window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  })

  it.each([
    [422, [{ msg: 'too short' }], 'The new password needs at least 12 characters.'],
    [429, 'Too many requests. Try again in 60 seconds.', 'Too many requests. Try again in 60 seconds.'],
  ])('shows the %i message', async (status, detail, shown) => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ detail }, status))))
    render(<ChangePasswordForm />)

    await fillAndSubmit()

    expect(await screen.findByRole('alert')).toHaveTextContent(shown)
  })
})
