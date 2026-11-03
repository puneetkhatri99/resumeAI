import { useState, type FormEvent } from 'react'
import { ApiError } from '../../shared/api/client.ts'
import { login, type CurrentUser } from './api.ts'
import './auth.css'

const NETWORK_ERROR_MESSAGE = 'Could not reach the server. Check your connection and try again.'

/**
 * The login page, shown in place of any page while nobody is logged in: email and password, then "Log in".
 * Errors (a wrong email or password, too many attempts, the database down) show the backend's message; a
 * network failure shows its own. On success it hands the user to `onLoggedIn`, and the app shows the page the
 * address asked for.
 */
export function LoginPage({ onLoggedIn }: { onLoggedIn: (user: CurrentUser) => void }) {
  const [isBusy, setIsBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setIsBusy(true)
    setError(null)
    try {
      onLoggedIn(await login(String(form.get('email')), String(form.get('password'))))
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : NETWORK_ERROR_MESSAGE)
      setIsBusy(false)
    }
  }

  return (
    <>
      <section className="page-hero">
        <div className="page-container">
          <div className="hero-heading">
            <h2 className="hero-title">Log in</h2>
            <p className="hero-text">Use the email and password your admin gave you.</p>
          </div>
        </div>
      </section>
      <div className="page-container page-body">
        <form className="card auth-form" onSubmit={(event) => void handleSubmit(event)}>
          <label className="field">
            <span className="field-label">Email</span>
            <input name="email" type="text" inputMode="email" autoComplete="username" required maxLength={254} />
          </label>
          <label className="field">
            <span className="field-label">Password</span>
            <input name="password" type="password" autoComplete="current-password" required maxLength={1024} />
          </label>
          {error && (
            <p role="alert" className="alert alert-error">
              {error}
            </p>
          )}
          <button type="submit" className="button button-primary" disabled={isBusy}>
            {isBusy ? 'Logging in…' : 'Log in'}
          </button>
        </form>
      </div>
    </>
  )
}
