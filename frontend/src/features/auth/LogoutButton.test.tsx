import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LogoutButton } from './LogoutButton.tsx'

describe('LogoutButton', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('logs out, then calls onLoggedOut', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(null, { status: 204 })))
    vi.stubGlobal('fetch', fetchMock)
    const onLoggedOut = vi.fn()
    render(<LogoutButton onLoggedOut={onLoggedOut} />)

    await userEvent.setup().click(screen.getByRole('button', { name: 'Log out' }))

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/logout', { method: 'POST' })
    expect(onLoggedOut).toHaveBeenCalledOnce()
  })

  it('still calls onLoggedOut when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))
    const onLoggedOut = vi.fn()
    render(<LogoutButton onLoggedOut={onLoggedOut} />)

    await userEvent.setup().click(screen.getByRole('button', { name: 'Log out' }))

    expect(onLoggedOut).toHaveBeenCalledOnce()
  })
})
