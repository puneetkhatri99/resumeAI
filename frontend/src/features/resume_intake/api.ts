import { ApiError, apiGet, apiPostEventStream } from '../../shared/api/client.ts'
import type { ResumeData } from '../resume_structuring/StructuredResume.tsx'

/** A stored intake. Mirrors ResumeIntakeResponse in backend/app/features/resume_intake/schemas.py. */
export type ResumeIntake = {
  id: number
  original_filename: string
  file_type: 'pdf' | 'docx' | 'doc' | 'txt'
  extracted_text: string
  /** Null only on intakes stored before AI extraction existed. */
  structured_data: ResumeData | null
  notice_period: string
  salary_expectation: string
  work_mode: 'remote' | 'hybrid' | 'onsite'
  rating: number
  strengths: string
  concerns: string
  overall_notes: string
  created_at: string
}

/** The progress stages the backend reports before its final event. */
export type IntakeStage = 'reading' | 'extracting'

/**
 * One Server-Sent Event of POST /api/resume_intake. Mirrors IntakeEvent in backend/app/features/resume_intake/schemas.py.
 * Every event carries all four fields; the unused ones are null.
 */
export type IntakeEvent =
  | { stage: IntakeStage; result: null; status: null; detail: null }
  | { stage: 'done'; result: ResumeIntake; status: null; detail: null }
  | { stage: 'error'; result: null; status: number; detail: string }

/**
 * Sends the interview form (the CV under `file` plus the seven answers) and resolves with the stored intake.
 * Calls `onStage` as the backend reports progress: "reading", then "extracting" (the AI step).
 * Throws ApiError on a non-2xx status or an "error" event (with the event's status and detail). A stream that
 * ends without a result (the server failed mid-stream) throws a plain Error; a network failure rejects with the
 * browser's TypeError.
 */
export async function submitResumeIntake(form: FormData, onStage: (stage: IntakeStage) => void): Promise<ResumeIntake> {
  let result: ResumeIntake | null = null
  await apiPostEventStream<IntakeEvent>('/resume_intake', form, (event) => {
    if (event.stage === 'error') throw new ApiError(event.status, event.detail)
    if (event.stage === 'done') result = event.result
    else onStage(event.stage)
  })
  if (result === null) throw new Error('The intake stream ended without a result.')
  return result
}

/** One row of the past-interviews list. Mirrors IntakeSummary in backend/app/features/resume_intake/schemas.py. */
export type IntakeSummary = {
  id: number
  created_at: string
  original_filename: string
  /** Null on intakes stored before AI extraction existed (show `original_filename` instead). */
  candidate_name: string | null
  candidate_email: string | null
  rating: number
  work_mode: ResumeIntake['work_mode']
  /** What the intake's AI calls cost in USD; null on intakes stored before AI usage was recorded. */
  ai_cost_usd: number | null
}

/** A page of past interviews, newest first. Mirrors IntakeList in backend/app/features/resume_intake/schemas.py. */
export type IntakeList = {
  items: IntakeSummary[]
  has_more: boolean
}

/** Lists past interviews, newest first, 20 at a time; with `beforeId`, the next page (those older than it). */
export function listResumeIntakes(beforeId?: number): Promise<IntakeList> {
  return apiGet<IntakeList>(beforeId === undefined ? '/resume_intake' : `/resume_intake?before_id=${beforeId}`)
}

/** Fetches one stored intake. Throws ApiError with status 404 when there is no intake with that id. */
export function getResumeIntake(id: number): Promise<ResumeIntake> {
  return apiGet<ResumeIntake>(`/resume_intake/${id}`)
}
