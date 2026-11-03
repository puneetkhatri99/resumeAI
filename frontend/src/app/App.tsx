import { useEffect, useState } from 'react'
import { flushSync } from 'react-dom'
import { AiUsagePanel } from '../features/ai_usage/AiUsagePanel.tsx'
import { AdminPage } from '../features/auth/AdminPage.tsx'
import { getCurrentUser, type CurrentUser } from '../features/auth/api.ts'
import { ChangePasswordForm } from '../features/auth/ChangePasswordForm.tsx'
import { LoginPage } from '../features/auth/LoginPage.tsx'
import { LogoutButton } from '../features/auth/LogoutButton.tsx'
import { AddCandidateForm } from '../features/candidates/AddCandidateForm.tsx'
import { CandidateList } from '../features/candidates/CandidateList.tsx'
import { HealthStatus } from '../features/health/HealthStatus.tsx'
import type { ResumeIntake } from '../features/resume_intake/api.ts'
import { IntakeDetail } from '../features/resume_intake/IntakeDetail.tsx'
import { IntakeHistory } from '../features/resume_intake/IntakeHistory.tsx'
import { InterviewForm } from '../features/resume_intake/InterviewForm.tsx'
import { UNAUTHORIZED_EVENT } from '../shared/api/client.ts'
import './app.css'
import { NavMenu } from './NavMenu.tsx'
import { ThemeToggle } from './ThemeToggle.tsx'

/** The path after "#" in the address, e.g. "/history/359"; "/" when there is none. */
function readHashPath(): string {
  return window.location.hash.slice(1) || '/'
}

/**
 * The current hash path, kept in sync with Back/Forward and link clicks, plus `navigate`, which changes the
 * address and renders the new page at once (so focus can move into it straight away).
 */
function useHashPath(): [string, (path: string) => void] {
  const [path, setPath] = useState(readHashPath)

  useEffect(() => {
    const update = () => setPath(readHashPath())
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])

  function navigate(to: string) {
    window.location.hash = to
    flushSync(() => setPath(to))
  }

  return [path, navigate]
}

const PLUS_ICON = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 5v14" />
    <path d="M5 12h14" />
  </svg>
)

/**
 * Root component: the top bar, then the page for the address, then the footer with the backend's health.
 * `#/` and `#/candidates` the candidate page, `#/candidates/new` the add-candidate form, `#/interview` the interview
 * form, `#/history` the past interviews, `#/history/{id}` one interview's result, `#/usage` the AI usage panel,
 * `#/password` the change-password form, `#/admin` the admin page (its link shows for admins only; the backend
 * answers anyone else with 403). Any other address shows the candidate page.
 * A successful interview submit opens its result at `#/history/{id}` (without fetching it again).
 *
 * The top bar: Candidates, an Interview menu (New interview, Past interviews), AI usage, the "Add candidate"
 * action, the theme switch and an account menu (Change password, Admin, Log out). On a phone the links fold into a
 * menu sheet, which closes on every page change.
 *
 * Nobody logged in (GET /api/auth/me fails, or any call later answers 401): the top bar without the navigation, and
 * the login page in place of the page. The address is kept, so after login the user lands where they meant to go.
 */
export function App() {
  const [path, navigate] = useHashPath()
  // 'loading' until /api/auth/me answers; null when nobody is logged in.
  const [user, setUser] = useState<'loading' | CurrentUser | null>('loading')
  // The intake just submitted, handed to its result page so it isn't fetched again.
  const [lastResult, setLastResult] = useState<ResumeIntake | null>(null)
  // The phone menu sheet is open only on the page it was opened on, so any page change closes it.
  const [menuOpenOn, setMenuOpenOn] = useState<string | null>(null)
  const isMenuOpen = menuOpenOn === path

  // Braces: an effect may only return a cleanup function, and some browsers' scrollTo returns a value.
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [path])

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch(() => setUser(null))
    const showLogin = () => setUser(null)
    window.addEventListener(UNAUTHORIZED_EVENT, showLogin)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, showLogin)
  }, [])

  function showResult(result: ResumeIntake) {
    setLastResult(result)
    navigate(`/history/${result.id}`)
  }

  function startNewInterview() {
    navigate('/interview')
    document.querySelector<HTMLInputElement>('input[name="file"]')?.focus()
  }

  const detailMatch = /^\/history\/(\d+)$/.exec(path)
  const isHistory = path.startsWith('/history')
  const isInterviewForm = path === '/interview'
  const isUsage = path === '/usage'
  const isAddCandidate = path === '/candidates/new'
  const isPassword = path === '/password'
  const isAdmin = path === '/admin'
  const isCandidates = !(isHistory || isInterviewForm || isUsage || isAddCandidate || isPassword || isAdmin)
  let page
  if (user === 'loading') {
    page = null
  } else if (user === null) {
    page = <LoginPage onLoggedIn={setUser} />
  } else if (detailMatch) {
    const intakeId = Number(detailMatch[1])
    page = <IntakeDetail key={intakeId} intakeId={intakeId} initialResult={lastResult} onReset={startNewInterview} />
  } else if (path === '/history') {
    page = <IntakeHistory />
  } else if (isInterviewForm) {
    page = <InterviewForm onSubmitted={showResult} />
  } else if (isUsage) {
    page = <AiUsagePanel />
  } else if (isAddCandidate) {
    page = <AddCandidateForm />
  } else if (isPassword) {
    page = <ChangePasswordForm />
  } else if (isAdmin) {
    page = <AdminPage />
  } else {
    page = <CandidateList />
  }
  const isLoggedIn = user !== 'loading' && user !== null

  return (
    <>
      <header className="app-header">
        <div className="page-container app-header-inner">
          <a className="app-brand" href="#/">
            <span className="app-logo" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
                <path d="M14 3v5h5" />
                <path d="M9 13h6" />
                <path d="M9 17h4" />
              </svg>
            </span>
            <h1>ResumeAI</h1>
          </a>
          {isLoggedIn ? (
            <>
              <button
                type="button"
                className="icon-button nav-toggle"
                aria-expanded={isMenuOpen}
                aria-controls="main-nav"
                aria-label={isMenuOpen ? 'Close menu' : 'Open menu'}
                onClick={() => setMenuOpenOn(isMenuOpen ? null : path)}
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  {isMenuOpen ? <path d="M6 6l12 12M18 6 6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
                </svg>
              </button>
              <nav id="main-nav" className="app-nav" aria-label="Main" data-open={isMenuOpen}>
                <a className="nav-link" href="#/candidates" aria-current={isCandidates ? 'page' : undefined}>
                  Candidates
                </a>
                <NavMenu label="Interview" isCurrent={isHistory || isInterviewForm}>
                  <a href="#/interview" aria-current={isInterviewForm ? 'page' : undefined}>
                    New interview
                    <span className="nav-menu-hint">CV plus your interview notes</span>
                  </a>
                  <a href="#/history" aria-current={isHistory ? 'page' : undefined}>
                    Past interviews
                    <span className="nav-menu-hint">Every submitted interview</span>
                  </a>
                </NavMenu>
                <a className="nav-link" href="#/usage" aria-current={isUsage ? 'page' : undefined}>
                  AI usage
                </a>
                <div className="app-nav-end">
                  <a className="button button-accent button-small" href="#/candidates/new" aria-current={isAddCandidate ? 'page' : undefined}>
                    {PLUS_ICON}
                    Add candidate
                  </a>
                  <span className="theme-toggle-row">
                    <ThemeToggle />
                  </span>
                  <NavMenu
                    label="Account"
                    alignEnd
                    isCurrent={isPassword || isAdmin}
                    trigger={<span className="avatar">{user.email.charAt(0)}</span>}
                  >
                    <p className="nav-menu-email">{user.email}</p>
                    <a href="#/password" aria-current={isPassword ? 'page' : undefined}>
                      Change password
                    </a>
                    {user.role === 'admin' && (
                      <a href="#/admin" aria-current={isAdmin ? 'page' : undefined}>
                        Admin
                      </a>
                    )}
                    <LogoutButton onLoggedOut={() => setUser(null)} />
                  </NavMenu>
                </div>
              </nav>
            </>
          ) : (
            <span className="app-nav-end">
              <ThemeToggle />
            </span>
          )}
        </div>
      </header>
      <main>
        <div key={path} className="page-enter">
          {page}
        </div>
      </main>
      <footer className="app-footer">
        <div className="page-container app-footer-inner">
          <span>ResumeAI · branded MinistryHub resumes</span>
          <HealthStatus />
        </div>
      </footer>
    </>
  )
}
