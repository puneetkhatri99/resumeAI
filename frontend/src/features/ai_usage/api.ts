import { apiGet } from '../../shared/api/client.ts'

/** Calls, tokens and cost over a set of AI calls. Mirrors UsageTotals in backend/app/features/ai_usage/schemas.py. */
export type UsageTotals = {
  calls: number
  /** Input plus output tokens. */
  tokens: number
  /** The priced calls' cost; 0 when there are none. */
  cost_usd: number
  /** Calls whose model has no price: the cost leaves them out. */
  unpriced_calls: number
}

/** The totals of one model or one call type (`key`). Mirrors UsageGroup. */
export type UsageGroup = UsageTotals & { key: string }

/** The totals of one day in the browser's time zone, e.g. `day: "2026-10-23"`. Mirrors DayUsage. */
export type DayUsage = UsageTotals & { day: string }

/** What an AI call was for: structuring the CV text, OCR of a scan, or the OCR retry. */
export type AiCallType = 'structuring' | 'ocr' | 'ocr_retry'

/** One recent AI call. Mirrors RecentAiCall in backend/app/features/ai_usage/schemas.py. */
export type RecentAiCall = {
  created_at: string
  model: string
  call_type: AiCallType
  tokens: number
  /** Null when the model has no price. */
  cost_usd: number | null
  /** Null for a failed upload's call. */
  resume_intake_id: number | null
  candidate_name: string | null
}

/** The AI usage panel's data. Mirrors AiUsageReport in backend/app/features/ai_usage/schemas.py. */
export type AiUsageReport = {
  this_month: UsageTotals
  all_time: UsageTotals
  by_model: UsageGroup[]
  by_call_type: UsageGroup[]
  /** The last 30 days, days with calls only, newest first. */
  by_day: DayUsage[]
  /** The newest 50 calls. */
  recent_calls: RecentAiCall[]
}

/** Fetches the AI usage, with days and "this month" in the browser's time zone. Throws ApiError on a non-2xx status. */
export function getAiUsage(): Promise<AiUsageReport> {
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone
  return apiGet<AiUsageReport>(`/ai_usage?tz=${encodeURIComponent(timeZone)}`)
}
