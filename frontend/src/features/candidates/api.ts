import { ApiError, apiGet, apiPostEventStream, apiRequest } from '../../shared/api/client.ts'

/** The progress stages the backend reports before its final event. */
export type CandidateStage = 'reading' | 'extracting' | 'generating' | 'uploading'

/**
 * One Server-Sent Event of POST /api/candidates. Mirrors CandidateEvent in backend/app/features/candidates/schemas.py.
 * Every event carries all four fields; the unused ones are null.
 */
export type CandidateEvent =
  | { stage: CandidateStage; new_resume_url: null; status: null; detail: null }
  | { stage: 'done'; new_resume_url: string; status: null; detail: null }
  | { stage: 'error'; new_resume_url: null; status: number; detail: string }

/** One row of the candidate page. Mirrors CandidateSummary in backend/app/features/candidates/schemas.py. */
export type CandidateSummary = {
  id: number
  name: string
  created_at: string
  /** "manual": added in this app; "api": sent to the public PDF API. */
  source: 'manual' | 'api'
  /** What the upload's AI calls cost in USD; null when none of them had a price. */
  ai_cost_usd: number | null
  /** A presigned link to the original CV that expires after 1 hour (reload the page for a fresh one). */
  original_resume_url: string
  /** The branded PDF's permanent public URL. */
  new_resume_url: string
}

/** A page of candidates, newest first. Mirrors CandidateList in backend/app/features/candidates/schemas.py. */
export type CandidateList = {
  items: CandidateSummary[]
  has_more: boolean
}

/**
 * Sends one CV (as the form field `file`) and resolves with the new branded resume's public URL.
 * Calls `onStage` as the backend reports progress: "reading", "extracting", "generating", then "uploading".
 * Throws ApiError on a non-2xx status or an "error" event (with the event's status and detail). A stream that
 * ends without a URL (the server failed mid-stream) throws a plain Error; a network failure rejects with the
 * browser's TypeError.
 */
export async function addCandidate(file: File, onStage: (stage: CandidateStage) => void): Promise<string> {
  const form = new FormData()
  form.append('file', file)
  let newResumeUrl: string | null = null
  await apiPostEventStream<CandidateEvent>('/candidates', form, (event) => {
    if (event.stage === 'error') throw new ApiError(event.status, event.detail)
    if (event.stage === 'done') newResumeUrl = event.new_resume_url
    else onStage(event.stage)
  })
  if (newResumeUrl === null) throw new Error('The candidate stream ended without a result.')
  return newResumeUrl
}

/** Lists candidates, newest first, 20 at a time; with `beforeId`, the next page (those older than it). */
export function listCandidates(beforeId?: number): Promise<CandidateList> {
  return apiGet<CandidateList>(beforeId === undefined ? '/candidates' : `/candidates?before_id=${beforeId}`)
}

/**
 * Deletes a candidate with both resume files. Resolves on 204; throws ApiError (with the backend's detail when it
 * sends one) on any other status, and a 401 sends the user to the login page.
 */
export function deleteCandidate(id: number): Promise<void> {
  return apiRequest('DELETE', `/candidates/${id}`)
}
