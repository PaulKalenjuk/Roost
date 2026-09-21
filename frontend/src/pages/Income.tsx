import { useEffect, useRef, useState } from 'react'
import {
  api,
  fetchList,
  money,
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
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [batch, setBatch] = useState<ImportBatch | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = async (id: number) => {
    setLoading(true)
    try {
      const [e, r] = await Promise.all([
        fetchList<MonthlyEarnings>(`/api/monthly-earnings/?listing=${id}`),
        fetchList<Reservation>(`/api/reservations/?listing=${id}`),
      ])
      setEarnings(e)
      setReservations(r)
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
    }
  }

  const grossTotal = earnings.reduce((s, e) => s + Number(e.gross_earnings), 0)
  const netTotal = earnings.reduce((s, e) => s + Number(e.total_earnings), 0)
  const feeTotal = earnings.reduce((s, e) => s + Number(e.service_fees), 0)

  return (
    <>
      <h1>Income</h1>
      <p className="sub">
        Import the Airbnb earnings report to bring in monthly totals. Re-importing
        overwrites the same months.
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
              <button onClick={importPdf} disabled={!listingId}>
                Import PDF
              </button>
            </div>
            <div className="hint">
              Airbnb → Payments &amp; payouts → Earnings → Download report.
            </div>
            {notice ? <Alert kind="ok">{notice}</Alert> : null}
            {error ? <Alert kind="err">{error}</Alert> : null}
            {batch ? (
              <Alert kind="info">
                Period {batch.period_start ?? '?'} → {batch.period_end ?? '?'}
                {batch.summary?.nights_booked ? ` · ${batch.summary.nights_booked} nights booked` : ''}
              </Alert>
            ) : null}
          </div>

          {loading ? <Loading what="income" /> : null}

          <div className="panel">
            <h2>Monthly earnings</h2>
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

          <div className="panel">
            <h2>Reservations (from CSV import)</h2>
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
        </>
      ) : null}
    </>
  )
}
