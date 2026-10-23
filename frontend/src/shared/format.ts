/**
 * A cost in US dollars with 4 decimals, e.g. "$0.0044" (a CV costs about $0.004), or "—" when there is none.
 * Bigger totals keep the 4 decimals and get no thousands separator, e.g. "$12.3456".
 */
export function formatUsd(cost: number | null): string {
  return cost === null ? '—' : `$${cost.toFixed(4)}`
}
