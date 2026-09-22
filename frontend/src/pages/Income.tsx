import { useEffect, useRef, useState } from 'react'
import {
  api,
  fetchList,
  money,
  type EarningsSummary,
  type ImportBatch,
  type Listing,
  type MonthlyEarnings,
  type Reservation,
} from '../api'
import { Alert, Field, Loading } from '../components'
import { useProperties } from '../store'

export default function Income() {
  const { selected } = useProperties()
  const [listings, setListings] = useState<Listing[]>([])
  const [listingId, setListingId] = useState<number | null>(null)
  const [earnings, setEarnings] = useState<MonthlyEarnings[]>([])
  const [reservations, setReservations] = useState<Reservation[]>([])
  const [summaries, setSummaries] = useState<EarningsSummary[]>([])
  const [imports, setImports] = useState<ImportBatch[]>([])
  const [nightsDraft, setNightsDraft] = useState<Record<number, string>>({})
  const [manualFy, setManualFy] = useState('')
  const [manualNights, setManualNights] = useState('')
  const [manualAvg, setManualAvg] = useState('')

  // Last six Australian financial years (1 Jul – 30 Jun), newest first.
  const fyOptions = (() => {
    const today = new Date()
    const startYear = today.getMonth() >= 6 ? today.getFullYear() : today.getFullYear() - 1
    return Array.from({ length: 6 }, (_, i) => {
      const year = startYear - i
      return {
        label: `FY${year}-${String(year + 1).slice(2)}`,
        start: `${year}-07-01`,
        end: `${year + 1}-06-30`,
      }
    })
  })()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [batch, setBatch] = useState<ImportBatch | null>(null)
  const [importing, setImporting] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = async (id: number) => {
    setLoading(true)
    try {
      const [e, r, s, i] = await Promise.all([
        fetchList<MonthlyEarnings>(`/api/monthly-earnings/?listing=${id}`),
        fetchList<Reservation>(`/api/reservations/?listing=${id}`),
        fetchList<EarningsSummary>(`/api/earnings-summaries/?listing=${id}`),
        fetchList<ImportBatch>(`/api/imports/?listing=${id}&source=airbnb_pdf`),
      ])
      setEarnings(e)
      setReservations(r)
      setSummaries(s)
      setImports(i)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!selected) {
      setListings([])
      setListingId(null)
      setEarnings([])
      setReservations([])
      setSummaries([])
      setImports([])
      return
    }
    fetchList<Listing>(`/api/listings/?property=${selected.id}`).then((ls) => {
      setListings(ls)
      setListingId(ls[0]?.id ?? null)
    })
  }, [selected?.id])

  useEffect(() => {
    if (listingId) load(listingId)
  }, [listingId])

  const importPdf = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file || !listingId) {
      setError('Pick a listing and a PDF first.')
      return
    }
    setError('')
    setNotice('')
    setBatch(null)
    setImporting(true)
    const body = new FormData()
    body.append('file', file)
    body.append('listing', String(listingId))
    try {
      const result = await api<ImportBatch>('/api/income/import-pdf/', { method: 'POST', body })
      setBatch(result)
      setNotice(
        `Imported ${result.rows_total} months (${result.rows_created} new, ${result.rows_updated} overwritten).`,
      )
      if (fileRef.current) fileRef.current.value = ''
      await load(listingId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed')
    } finally {
      setImporting(false)
    }
  }

  const saveNights = async (id: number) => {
    const draft = nightsDraft[id]
    if (draft === undefined) return
    try {
      await api(`/api/earnings-summaries/${id}/`, {
        method: 'PATCH',
        body: JSON.stringify({ nights_booked: draft === '' ? null : Number(draft) }),
      })
      setNightsDraft((d) => {
        const copy = { ...d }
        delete copy[id]
        return copy
      })
      setNotice('Nights booked updated.')
      if (listingId) await load(listingId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }

  const saveManualNights = async () => {
    if (!listingId) return
    const option = fyOptions.find((o) => o.label === manualFy)
    if (!option) {
      setError('Pick a financial year.')
      return
    }
    if (manualNights === '') {
      setError('Enter the number of nights.')
      return
    }
    setError('')
    setNotice('')
    const inFy = summaries.filter((s) => s.financial_year === option.label)
    // Prefer an exact period match; fall back to a lone record for that year.
    const target =
      inFy.find((s) => s.period_start === option.start && s.period_end === option.end) ??
      (inFy.length === 1 ? inFy[0] : undefined)
    const payload = {
      listing: listingId,
      period_start: option.start,
      period_end: option.end,
      nights_booked: Number(manualNights),
      avg_night_stay: manualAvg === '' ? null : manualAvg,
    }
    try {
      if (target) {
        await api(`/api/earnings-summaries/${target.id}/`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
        setNotice(`Updated nights for ${option.label}.`)
      } else {
        await api('/api/earnings-summaries/', {
          method: 'POST',
          body: JSON.stringify(payload),
        })
        setNotice(`Recorded ${manualNights} nights for ${option.label}.`)
      }
      setManualNights('')
      setManualAvg('')
      await load(listingId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }

  const deleteSummary = async (summary: EarningsSummary) => {
    const when = `${summary.period_start ?? '?'} → ${summary.period_end ?? '?'}`
    if (!window.confirm(`Delete the nights record for ${summary.financial_year || when}?`)) return
    try {
      await api(`/api/earnings-summaries/${summary.id}/`, { method: 'DELETE' })
      if (listingId) await load(listingId)
      setNotice('Nights record deleted.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  const grossTotal = earnings.reduce((s, e) => s + Number(e.gross_earnings), 0)
  const netTotal = earnings.reduce((s, e) => s + Number(e.total_earnings), 0)
  const feeTotal = earnings.reduce((s, e) => s + Number(e.service_fees), 0)

  // Group the period summaries by financial year for the nights table.
  const byFy = new Map<string, EarningsSummary[]>()
  for (const summary of summaries) {
    const key = summary.financial_year || '—'
    const existing = byFy.get(key)
    if (existing) existing.push(summary)
    else byFy.set(key, [summary])
  }

  return (
    <>
      <h1>Income</h1>
      <p className="sub">
        Import the Airbnb earnings report to bring in monthly totals. Re-importing
        overwrites the same months. The PDF you upload is kept with the import, so
        every figure stays traceable to its source.
      </p>

      {!selected ? (
        <Alert kind="info">Create a property first, on the Property setup page.</Alert>
      ) : null}

      {selected ? (
        <>
          <div className="panel">
            <h2>Import earnings report (PDF)</h2>
            <div className="row">
              <div>
                <label>Listing</label>
                <select
                  value={listingId ?? ''}
                  onChange={(e) => setListingId(e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">— select listing —</option>
                  {listings.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label>Airbnb earnings PDF</label>
                <input type="file" accept="application/pdf" ref={fileRef} />
              </div>
              <button className="btn-busy" onClick={importPdf} disabled={!listingId || importing}>
                {importing ? (
                  <>
                    <span className="spinner" />
                    Importing…
                  </>
                ) : (
                  'Import PDF'
                )}
              </button>
            </div>
            <div className="hint">
              Airbnb → Payments &amp; payouts → Earnings → Download report.
            </div>
            {importing ? (
              <Alert kind="info">
                <span className="spinner" />
                Reading the earnings report and updating months — one moment…
              </Alert>
            ) : null}
            {notice ? <Alert kind="ok">{notice}</Alert> : null}
            {error ? <Alert kind="err">{error}</Alert> : null}
            {batch ? (
              <Alert kind="info">
                Period {batch.period_start ?? '?'} → {batch.period_end ?? '?'}
                {batch.summary?.nights_booked ? ` · ${batch.summary.nights_booked} nights booked` : ''}
                {batch.report_url ? (
                  <>
                    {' · '}
                    <a href={batch.report_url} target="_blank" rel="noreferrer">
                      View the PDF just imported
                    </a>
                  </>
                ) : null}
              </Alert>
            ) : null}
          </div>

          <div className="panel">
            <h2>Import history</h2>
            <p className="sub">
              Every earnings report you've imported is kept, so any figure can be traced
              back to the document it came from.
            </p>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Imported</th>
                    <th>Period</th>
                    <th>File</th>
                    <th className="num">Months</th>
                  </tr>
                </thead>
                <tbody>
                  {imports.map((b) => (
                    <tr key={b.id}>
                      <td>{b.created_at?.slice(0, 10)}</td>
                      <td>
                        {b.period_start ?? '?'} → {b.period_end ?? '?'}
                      </td>
                      <td>
                        {b.report_url ? (
                          <a href={b.report_url} target="_blank" rel="noreferrer">
                            {b.filename || 'earnings-report.pdf'}
                          </a>
                        ) : (
                          <span className="muted">
                            {b.filename || '—'} (no file kept)
                          </span>
                        )}
                      </td>
                      <td className="num">{b.rows_total}</td>
                    </tr>
                  ))}
                  {imports.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="muted">
                        No earnings reports imported yet.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>

          {loading ? <Loading what="income" /> : null}

          <div className="panel">
            <h2>Monthly earnings</h2>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Month</th>
                    <th className="num">Gross</th>
                    <th className="num">Service fees</th>
                    <th className="num">Net</th>
                  </tr>
                </thead>
                <tbody>
                  {earnings.map((e) => (
                    <tr key={e.id}>
                      <td>{e.month?.slice(0, 7)}</td>
                      <td className="num">{money(e.gross_earnings)}</td>
                      <td className="num">{money(e.service_fees)}</td>
                      <td className="num">{money(e.total_earnings)}</td>
                    </tr>
                  ))}
                  {earnings.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="muted">
                        No monthly earnings imported yet.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
                {earnings.length ? (
                  <tfoot>
                    <tr>
                      <td>Total</td>
                      <td className="num">{money(grossTotal)}</td>
                      <td className="num">{money(feeTotal)}</td>
                      <td className="num">{money(netTotal)}</td>
                    </tr>
                  </tfoot>
                ) : null}
              </table>
            </div>
          </div>

          <div className="panel">
            <h2>Nights booked per financial year</h2>
            <p className="sub">
              Read from the earnings report when you import it. Edit and save if a figure needs
              correcting.
            </p>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Financial year</th>
                    <th>Period</th>
                    <th className="num">Nights booked</th>
                    <th className="num">Avg night stay</th>
                    <th className="num">Gross</th>
                    <th className="num">Net</th>
                  </tr>
                </thead>
                {[...byFy.entries()].map(([fy, group]) => {
                  const totalNights = group.reduce((n, s) => n + (s.nights_booked ?? 0), 0)
                  const gross = group.reduce((n, s) => n + Number(s.gross_earnings), 0)
                  const net = group.reduce((n, s) => n + Number(s.total_earnings), 0)
                  return (
                    <tbody key={fy}>
                      <tr className="selected-row">
                        <td>
                          <strong>{fy}</strong>
                        </td>
                        <td className="muted">total</td>
                        <td className="num">
                          <strong>{totalNights}</strong>
                        </td>
                        <td className="num">—</td>
                        <td className="num">{money(gross)}</td>
                        <td className="num">{money(net)}</td>
                      </tr>
                      {group.map((s) => (
                        <tr key={s.id}>
                          <td className="muted">
                            {s.period_start ?? '?'} → {s.period_end ?? '?'}
                          </td>
                          <td />
                          <td className="num">
                            <input
                              type="number"
                              min="0"
                              style={{ width: 90, display: 'inline-block' }}
                              value={nightsDraft[s.id] ?? s.nights_booked ?? ''}
                              onChange={(e) =>
                                setNightsDraft({ ...nightsDraft, [s.id]: e.target.value })
                              }
                            />
                            {nightsDraft[s.id] !== undefined ? (
                              <button
                                type="button"
                                className="ghost small"
                                onClick={() => saveNights(s.id)}
                              >
                                Save
                              </button>
                            ) : null}
                            <button
                              type="button"
                              className="ghost small"
                              onClick={() => deleteSummary(s)}
                            >
                              Delete
                            </button>
                          </td>
                          <td className="num">{s.avg_night_stay ?? '—'}</td>
                          <td className="num">{money(s.gross_earnings)}</td>
                          <td className="num">{money(s.total_earnings)}</td>
                        </tr>
                      ))}
                    </tbody>
                  )
                })}
                {summaries.length === 0 ? (
                  <tbody>
                    <tr>
                      <td colSpan={6} className="muted">
                        Nothing yet — import an earnings report above, or add a year below.
                      </td>
                    </tr>
                  </tbody>
                ) : null}
              </table>
            </div>

            <h3>Add or correct a financial year</h3>
            <div className="row">
              <div>
                <label>Financial year</label>
                <select value={manualFy} onChange={(e) => setManualFy(e.target.value)}>
                  <option value="">— select —</option>
                  {fyOptions.map((o) => (
                    <option key={o.label} value={o.label}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label>Nights booked</label>
                <input
                  type="number"
                  min="0"
                  value={manualNights}
                  onChange={(e) => setManualNights(e.target.value)}
                />
              </div>
              <div>
                <label>Avg night stay (optional)</label>
                <input
                  type="number"
                  step="0.01"
                  value={manualAvg}
                  onChange={(e) => setManualAvg(e.target.value)}
                />
              </div>
              <button
                type="button"
                className="ghost small"
                onClick={saveManualNights}
                disabled={!listingId}
              >
                Save nights
              </button>
            </div>
            <div className="hint">
              Works with or without an imported report — saving a year that already has a record
              updates it instead of adding a second.
            </div>
          </div>

          <div className="panel">
            <h2>Reservations (from CSV import)</h2>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Confirmation</th>
                    <th>Guest</th>
                    <th>Check-in</th>
                    <th>Nights</th>
                    <th className="num">Gross</th>
                    <th className="num">Net</th>
                  </tr>
                </thead>
                <tbody>
                  {reservations.map((r) => (
                    <tr key={r.id}>
                      <td>{r.confirmation_code}</td>
                      <td>{r.guest_name || '—'}</td>
                      <td>{r.check_in ?? '—'}</td>
                      <td>{r.nights ?? '—'}</td>
                      <td className="num">{money(r.gross_earnings)}</td>
                      <td className="num">{money(r.net_payout)}</td>
                    </tr>
                  ))}
                  {reservations.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="muted">
                        No reservations yet (import a transaction CSV via the Django
                        admin, or wait for the CSV importer in this UI).
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>
        </>
      ) : null}
    </>
  )
}
