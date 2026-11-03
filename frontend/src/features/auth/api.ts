import { apiGet, apiRequest } from '../../shared/api/client.ts'

/** The logged-in user. Mirrors CurrentUser in backend/app/features/auth/schemas.py. */
export type CurrentUser = {
  email: string
  role: 'admin' | 'recruiter'
}

/** One recruiter account on the admin page. Mirrors Recruiter in backend/app/features/auth/schemas.py. */
export type Recruiter = {
  id: number
  email: string
  created_at: string
}

/** Every password set anywhere (add, reset, change) needs at least this many characters (the backend checks too). */
export const MIN_PASSWORD_LENGTH = 12

/** Logs in; the backend sets the session cookie. Throws ApiError 401 for a wrong email or password, 429 when rate-limited. */
export function login(email: string, password: string): Promise<CurrentUser> {
  return apiRequest<CurrentUser>('POST', '/auth/login', { email, password })
}

/** Ends the session and clears the cookie. */
export function logout(): Promise<void> {
  return apiRequest('POST', '/auth/logout')
}

/** The logged-in user; throws ApiError 401 when nobody is logged in. */
export function getCurrentUser(): Promise<CurrentUser> {
  return apiGet<CurrentUser>('/auth/me')
}

/** Changes the user's own password and ends their other sessions. A wrong current password is ApiError 400. */
export function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return apiRequest('POST', '/auth/password', { current_password: currentPassword, new_password: newPassword })
}

/** Every recruiter, sorted by email (admins only: ApiError 403 otherwise). */
export async function listRecruiters(): Promise<Recruiter[]> {
  return (await apiGet<{ items: Recruiter[] }>('/auth/recruiters')).items
}

/** Adds a recruiter with the password the admin sets. ApiError 409 when the email is taken. */
export function addRecruiter(email: string, password: string): Promise<Recruiter> {
  return apiRequest<Recruiter>('POST', '/auth/recruiters', { email, password })
}

/** Sets a recruiter's password and ends their sessions. ApiError 404 when they no longer exist. */
export function resetRecruiterPassword(id: number, password: string): Promise<void> {
  return apiRequest('PUT', `/auth/recruiters/${id}/password`, { password })
}

/** Removes a recruiter and their sessions. ApiError 404 when they no longer exist. */
export function removeRecruiter(id: number): Promise<void> {
  return apiRequest('DELETE', `/auth/recruiters/${id}`)
}
