import { logout } from './api.ts'

/**
 * The header's "Log out": ends the session, then calls `onLoggedOut`. Also on failure: a cookie left behind only
 * gets a 401, which shows the login page anyway.
 */
export function LogoutButton({ onLoggedOut }: { onLoggedOut: () => void }) {
  async function handleClick() {
    try {
      await logout()
    } catch {
      // Nothing to show: the user is sent to the login page either way.
    }
    onLoggedOut()
  }

  return (
    <button type="button" onClick={() => void handleClick()}>
      Log out
    </button>
  )
}
