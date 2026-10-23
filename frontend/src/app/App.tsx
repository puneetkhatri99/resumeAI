import { useEffect, useState } from 'react'
import { flushSync } from 'react-dom'
import { AiUsagePanel } from '../features/ai_usage/AiUsagePanel.tsx'
import { HealthStatus } from '../features/health/HealthStatus.tsx'
import type { ResumeIntake } from '../features/resume_intake/api.ts'
import { IntakeDetail } from '../features/resume_intake/IntakeDetail.tsx'
import { IntakeHistory } from '../features/resume_intake/IntakeHistory.tsx'
import { InterviewForm } from '../features/resume_intake/InterviewForm.tsx'
import './app.css'

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

/**
 * Root component: the header band with the navigation, then the page for the address:
 * `#/` the interview form, `#/history` the past interviews, `#/history/{id}` one interview's result,
 * `#/usage` the AI usage panel. Any other address shows the form.
 * A successful submit opens its result at `#/history/{id}` (without fetching it again).
 */
export function App() {
  const [path, navigate] = useHashPath()
  // The intake just submitted, handed to its result page so it isn't fetched again.
  const [lastResult, setLastResult] = useState<ResumeIntake | null>(null)

  // Braces: an effect may only return a cleanup function, and some browsers' scrollTo returns a value.
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [path])

  function showResult(result: ResumeIntake) {
    setLastResult(result)
    navigate(`/history/${result.id}`)
  }

  function startNewInterview() {
    navigate('/')
    document.querySelector<HTMLInputElement>('input[name="file"]')?.focus()
  }

  const detailMatch = /^\/history\/(\d+)$/.exec(path)
  const isHistory = path.startsWith('/history')
  const isUsage = path === '/usage'
  let page
  if (detailMatch) {
    const intakeId = Number(detailMatch[1])
    page = <IntakeDetail key={intakeId} intakeId={intakeId} initialResult={lastResult} onReset={startNewInterview} />
  } else if (path === '/history') {
    page = <IntakeHistory />
  } else if (isUsage) {
    page = <AiUsagePanel />
  } else {
    page = <InterviewForm onSubmitted={showResult} />
  }

  return (
    <>
      <header className="app-header band">
        <div className="page-container app-header-inner">
          <div className="app-brand">
            <span className="app-logo" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
                <path d="M14 3v5h5" />
                <path d="M9 13h6" />
                <path d="M9 17h4" />
              </svg>
            </span>
            <div>
              <h1>ResumeAI</h1>
              <span className="app-tagline">Recruiter intake</span>
            </div>
          </div>
          <nav className="app-nav" aria-label="Main">
            <a href="#/" aria-current={isHistory || isUsage ? undefined : 'page'}>
              New interview
            </a>
            <a href="#/history" aria-current={isHistory ? 'page' : undefined}>
              Past interviews
            </a>
            <a href="#/usage" aria-current={isUsage ? 'page' : undefined}>
              AI usage
            </a>
          </nav>
          <HealthStatus />
        </div>
      </header>
      <main>{page}</main>
    </>
  )
}
