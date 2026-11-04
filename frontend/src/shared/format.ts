/**
 * A cost in US dollars with 4 decimals, e.g. "$0.0044" (a CV costs about $0.004), or "—" when there is none.
 * Bigger totals keep the 4 decimals and get no thousands separator, e.g. "$12.3456".
 */
export function formatUsd(cost: number | null): string {
  return cost === null ? '—' : `$${cost.toFixed(4)}`
}

/**
 * When something happened, short enough to scan down a list: "Today, 8:42 PM", "Yesterday, 8:42 PM", then
 * "Nov 1" this year and "Nov 1, 2025" before that. Days follow the browser's time zone.
 */
export function formatWhen(iso: string, now = new Date()): string {
  const date = new Date(iso)
  const dayNumber = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() / 86_400_000
  // Rounded, so a daylight-saving day of 23 or 25 hours still counts as one day.
  const daysAgo = Math.round(dayNumber(now) - dayNumber(date))
  const time = date.toLocaleTimeString(undefined, { timeStyle: 'short' })
  if (daysAgo === 0) return `Today, ${time}`
  if (daysAgo === 1) return `Yesterday, ${time}`
  const sameYear = date.getFullYear() === now.getFullYear()
  return date.toLocaleDateString(undefined, sameYear ? { month: 'short', day: 'numeric' } : { dateStyle: 'medium' })
}

/** The full date and time, e.g. "Nov 4, 2026, 8:42 PM": the hover title behind formatWhen. */
export function formatFullDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
