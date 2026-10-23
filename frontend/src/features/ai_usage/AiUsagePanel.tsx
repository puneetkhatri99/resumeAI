import { useEffect, useState } from 'react'
import { formatUsd } from '../../shared/format.ts'
import { getAiUsage, type AiCallType, type AiUsageReport, type UsageTotals } from './api.ts'
import './aiUsage.css'

const CALL_TYPE_LABELS: Record<AiCallType, string> = {
  structuring: 'Structuring',
  ocr: 'OCR',
  ocr_retry: 'OCR retry',
}
const LOAD_ERROR_MESSAGE = 'Could not load the AI usage.'
const NO_CALLS_MESSAGE = 'No AI calls.'

/**
 * The warning that a cost leaves out some calls, e.g. "+ 3 unpriced calls" (their model is missing from the
 * backend's price table). Nothing when every call has a price.
 */
function UnpricedNote({ count }: { count: number }) {
  if (count === 0) return null
  return <span className="usage-unpriced">{`+ ${count} unpriced call${count === 1 ? '' : 's'}`}</span>
}

/** One period's calls, tokens and cost, as a card. */
function TotalsCard({ title, totals }: { title: string; totals: UsageTotals }) {
  return (
    <section className="card usage-total" aria-label={title}>
      <h3>{title}</h3>
      <p className="usage-total-cost">{formatUsd(totals.cost_usd)}</p>
      <UnpricedNote count={totals.unpriced_calls} />
      <dl className="usage-total-figures">
        <div>
          <dt>Calls</dt>
          <dd>{totals.calls.toLocaleString()}</dd>
        </div>
        <div>
          <dt>Tokens</dt>
          <dd>{totals.tokens.toLocaleString()}</dd>
        </div>
      </dl>
    </section>
  )
}

/** A table of totals per group (a model, a call type or a day); `label` is each row's first cell. */
function UsageTable({
  caption,
  labelHeader,
  rows,
}: {
  caption: string
  labelHeader: string
  rows: (UsageTotals & { label: string })[]
}) {
  return (
    <div className="card usage-table-card">
      <table className="usage-table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{labelHeader}</th>
            <th scope="col">Calls</th>
            <th scope="col">Tokens</th>
            <th scope="col">Cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}</th>
              <td>{row.calls.toLocaleString()}</td>
              <td>{row.tokens.toLocaleString()}</td>
              <td>
                {formatUsd(row.cost_usd)} <UnpricedNote count={row.unpriced_calls} />
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={4}>{NO_CALLS_MESSAGE}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

/**
 * The AI usage panel (#/usage): what the OpenAI calls cost, this month and all time, by model, by call type,
 * per day (the last 30 days) and the newest calls, each linked to its interview when the upload succeeded.
 * Each call is priced at the model that made it; days and "this month" follow the browser's time zone.
 */
export function AiUsagePanel() {
  const [report, setReport] = useState<AiUsageReport | null>(null)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')

  function fetchReport() {
    getAiUsage()
      .then((loaded) => {
        setReport(loaded)
        setStatus('ready')
      })
      .catch(() => setStatus('error'))
  }

  useEffect(() => {
    fetchReport()
  }, [])

  function tryAgain() {
    setStatus('loading')
    fetchReport()
  }

  return (
    <>
      <section className="page-hero band">
        <div className="page-container">
          <div className="hero-heading">
            <span className="hero-eyebrow">AI usage</span>
            <h2 className="hero-title">AI usage and cost</h2>
            <p className="hero-text">
              Every OpenAI call, priced at the model that made it. Days and months follow your time zone. A call that
              got no answer (a timeout) isn't counted, so OpenAI's billing page has the final figures.
            </p>
          </div>
        </div>
      </section>
      <div className="page-container page-body">
        {status === 'loading' && (
          <p role="status" className="card usage-message">
            Loading…
          </p>
        )}
        {status === 'error' && (
          <div className="card usage-message">
            <p role="alert" className="alert alert-error">
              {LOAD_ERROR_MESSAGE}
            </p>
            <button type="button" className="button button-outline" onClick={tryAgain}>
              Try again
            </button>
          </div>
        )}
        {status === 'ready' && report && (
          <div className="usage">
            <div className="usage-totals">
              <TotalsCard title="This month" totals={report.this_month} />
              <TotalsCard title="All time" totals={report.all_time} />
            </div>
            <UsageTable
              caption="By model"
              labelHeader="Model"
              rows={report.by_model.map((group) => ({ ...group, label: group.key }))}
            />
            <UsageTable
              caption="By call type"
              labelHeader="Type"
              rows={report.by_call_type.map((group) => ({
                ...group,
                label: CALL_TYPE_LABELS[group.key as AiCallType] ?? group.key,
              }))}
            />
            <UsageTable
              caption="Cost per day (last 30 days)"
              labelHeader="Day"
              rows={report.by_day.map((day) => ({
                ...day,
                label: new Date(`${day.day}T00:00`).toLocaleDateString(undefined, { dateStyle: 'medium' }),
              }))}
            />
            <div className="card usage-table-card">
              <table className="usage-table">
                <caption>Recent AI calls</caption>
                <thead>
                  <tr>
                    <th scope="col">Time</th>
                    <th scope="col">Model</th>
                    <th scope="col">Type</th>
                    <th scope="col">Tokens</th>
                    <th scope="col">Cost</th>
                    <th scope="col">Candidate</th>
                  </tr>
                </thead>
                <tbody>
                  {report.recent_calls.map((call, index) => (
                    <tr key={index}>
                      <td>
                        {new Date(call.created_at).toLocaleString(undefined, {
                          dateStyle: 'medium',
                          timeStyle: 'short',
                        })}
                      </td>
                      <td>{call.model}</td>
                      <td>{CALL_TYPE_LABELS[call.call_type]}</td>
                      <td>{call.tokens.toLocaleString()}</td>
                      {/* One call with a null cost: its model has no price. */}
                      <td>{call.cost_usd === null ? 'no price' : formatUsd(call.cost_usd)}</td>
                      <td>
                        {call.resume_intake_id === null ? (
                          '—'
                        ) : (
                          <a href={`#/history/${call.resume_intake_id}`}>
                            {call.candidate_name ?? `Interview ${call.resume_intake_id}`}
                          </a>
                        )}
                      </td>
                    </tr>
                  ))}
                  {report.recent_calls.length === 0 && (
                    <tr>
                      <td colSpan={6}>{NO_CALLS_MESSAGE}</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
