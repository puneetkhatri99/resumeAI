import { useEffect, useState } from 'react'

type Theme = 'light' | 'dark'

// localStorage key; index.html reads the same key before first paint, so a remembered theme never flashes.
export const THEME_STORAGE_KEY = 'theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

/** The theme on screen: the user's pick (<html data-theme>) or, until they pick, the system's. */
function readTheme(): Theme {
  const picked = document.documentElement.dataset.theme
  if (picked === 'light' || picked === 'dark') return picked
  return window.matchMedia?.(DARK_QUERY).matches ? 'dark' : 'light'
}

/**
 * The light/dark switch in the top bar. The theme follows the system until clicked; the click sets
 * <html data-theme> (app.css swaps every colour token on it) and remembers the pick in this browser.
 * Where the browser has view transitions, the old and new colours cross-fade instead of snapping.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme)

  // While nothing is picked, a system change (e.g. night mode at sunset) moves the icon with the colours.
  useEffect(() => {
    const query = window.matchMedia?.(DARK_QUERY)
    const update = () => setTheme(readTheme())
    query?.addEventListener('change', update)
    return () => query?.removeEventListener('change', update)
  }, [])

  function toggle() {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    const apply = () => {
      document.documentElement.dataset.theme = next
      setTheme(next)
    }
    if (document.startViewTransition && !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      document.startViewTransition(apply)
    } else {
      apply()
    }
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next)
    } catch {
      // Storage blocked (private window): the pick still holds until the tab closes.
    }
  }

  const label = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'
  return (
    <button type="button" className="icon-button theme-toggle" aria-label={label} title={label} onClick={toggle}>
      {theme === 'dark' ? (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      )}
    </button>
  )
}
