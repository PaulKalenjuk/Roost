import { useEffect, useState } from 'react'
import { api, money, pct } from '../api'
import { Alert, Field, Loading } from '../components'
import { useProperties } from '../store'

type Num = number | string | null

interface MonthRow {
  month: string
  listing: string
  gross_earnings: Num
  service_fees: Num
  tax_withheld: Num
  total_earnings: Num
}
interface Report {
  property: string
  financial_year: string
  period: [string, string]
  let_share: Num
  gst_registered: boolean
  income: {
    gross_earnings: Num
    service_fees: Num
    tax_withheld: Num
    total_earnings: Num
    nights_booked: number
    months: MonthRow[]
  }
  expenses: {
    total_amount: Num
    total_deductible: Num
    by_category: Record<string, { amount: Num; deductible: Num }>
    notes?: string[]
    items: Array<{ date: string; category: string; description: string; amount: Num; apportionment: string; share: Num; deductible_amount: Num; detail?: string }>
  }
  depreciation: {
    total_deduction: Num
    lines: Array<{ asset: string; method: string; opening_value: Num; business_use_pct: Num; deduction: Num; closing_value: Num; days_held: number | null }>
  }
  net_rental_result: Num
}
interface OwnerView {
  owner: string
  owner_email: string
  share: Num
  income: { gross_earnings: Num; total_earnings: Num }
  expenses_by_category: Record<string, { deductible: Num }>
  expenses_deductible: Num
  depreciation: Num
  net_rental_result: Num
}
interface ReportResponse {
  report: Report
  owners?: OwnerView[]
  fy_options: string[]
}

export default function Reporting() {
  const { selected } = useProperties()
  const [fy, setFy] = useState('')
  const [fyOptions, setFyOptions] = useState<string[]>([])
  const [includeOwners, setIncludeOwners] = useState(true)
  const [recompute, setRecompute] = useState(false)
  const [data, setData] = useState<ReportResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const run = async () => {
    if (!selected) return
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({ property: String(selected.id) })
      if (fy) params.set('fy', fy)
      if (includeOwners) params.set('owners', '1')
      if (recompute) params.set('recompute', '1')
      const result = await api<ReportResponse>(`/api/reports/fy/?${params}`)
      setData(result)
      if (!fy && result.fy_options.length) setFy(result.report.financial_year)
      setFyOptions(result.fy_options)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Report failed')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setData(null)
    if (selected) run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id])

  const report = data?.report

  return (
    <>
      <h1>Reporting</h1>
      <p className="sub">Per financial year (1 July – 30 June), with the maths shown and an optional per-owner split.</p>

      {!selected ? <Alert kind="info">Create a property first.</Alert> : null}

      <div className="panel">
        <div className="row">
          <div>
            <label>Financial year</label>
            <select value={fy} onChange={(e) => setFy(e.target.value)}>
              {(fyOptions.length ? fyOptions : ['']).map((o) => (
                <option key={o} value={o}>
                  {o || 'current'}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Split by owner</label>
            <select value={String(includeOwners)} onChange={(e) => setIncludeOwners(e.target.value === 'true')}>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </div>
          <div>
            <label>Rebuild depreciation</label>
            <select value={String(recompute)} onChange={(e) => setRecompute(e.target.value === 'true')}>
              <option value="false">No</option>
              <option value="true">Yes</option>
            </select>
          </div>
          <button onClick={run} disabled={!selected}>
            Run report
          </button>
        </div>
      </div>

      {error ? <Alert kind="err">{error}</Alert> : null}
      {loading ? <Loading what="report" /> : null}

      {report ? (
        <>
          <div className="panel">
            <h2>
              {report.property} — {report.financial_year}
            </h2>
            <p className="muted">
              {report.period?.[0]} → {report.period?.[1]} · Let share {pct(report.let_share)} · GST{' '}
              {report.gst_registered ? 'registered' : 'not registered'}
            </p>

            <h3>Income</h3>
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
                {report.income.months.map((m) => (
                  <tr key={m.month}>
                    <td>{String(m.month).slice(0, 7)}</td>
                    <td className="num">{money(m.gross_earnings)}</td>
                    <td className="num">{money(m.service_fees)}</td>
                    <td className="num">{money(m.total_earnings)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td>Net income ({report.income.nights_booked} nights)</td>
                  <td className="num">{money(report.income.gross_earnings)}</td>
                  <td className="num">{money(report.income.service_fees)}</td>
                  <td className="num">{money(report.income.total_earnings)}</td>
                </tr>
              </tfoot>
            </table>

            <h3>Expenses (claimable portion)</h3>
            <table>
              <thead>
                <tr>
                  <th>Category</th>
                  <th className="num">Amount</th>
                  <th className="num">Claimable</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(report.expenses.by_category).map(([name, v]) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td className="num">{money(v.amount)}</td>
                    <td className="num">{money(v.deductible)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td>Total claimable</td>
                  <td className="num">{money(report.expenses.total_amount)}</td>
                  <td className="num">{money(report.expenses.total_deductible)}</td>
                </tr>
              </tfoot>
            </table>

            {report.expenses.notes?.length ? (
              <p className="muted" style={{ marginTop: 8 }}>
                Apportionment: {report.expenses.notes.join('; ')}
              </p>
            ) : null}

            <h3>Depreciation</h3>
            <table>
              <thead>
                <tr>
                  <th>Asset</th>
                  <th>Method</th>
                  <th className="num">Opening</th>
                  <th className="num">Business use</th>
                  <th className="num">Deduction</th>
                </tr>
              </thead>
              <tbody>
                {report.depreciation.lines.map((l, i) => (
                  <tr key={i}>
                    <td>{l.asset}</td>
                    <td>{l.method}</td>
                    <td className="num">{money(l.opening_value)}</td>
                    <td className="num">{pct(l.business_use_pct)}</td>
                    <td className="num">{money(l.deduction)}</td>
                  </tr>
                ))}
                {report.depreciation.lines.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="muted">
                      No depreciation entries.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>

            <h2 className="totals" style={{ marginTop: 18 }}>
              Net rental result: {money(report.net_rental_result)}
            </h2>
          </div>

          {data?.owners?.length ? (
            <div className="panel">
              <h2>Per-owner breakdown</h2>
              {data.owners.map((o) => (
                <div key={o.owner} className="card" style={{ marginBottom: 12 }}>
                  <h3>
                    {o.owner} <span className="badge ok">{pct(o.share)}</span>
                  </h3>
                  <table>
                    <tbody>
                      <tr>
                        <td>Income (net)</td>
                        <td className="num">{money(o.income.total_earnings)}</td>
                      </tr>
                      {Object.entries(o.expenses_by_category).map(([name, v]) => (
                        <tr key={name}>
                          <td className="muted">— {name}</td>
                          <td className="num">{money(v.deductible)}</td>
                        </tr>
                      ))}
                      <tr>
                        <td>Expenses (claimable)</td>
                        <td className="num">{money(o.expenses_deductible)}</td>
                      </tr>
                      <tr>
                        <td>Depreciation</td>
                        <td className="num">{money(o.depreciation)}</td>
                      </tr>
                      <tr>
                        <td>
                          <strong>Net rental result</strong>
                        </td>
                        <td className="num">
                          <strong>{money(o.net_rental_result)}</strong>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          ) : null}
        </>
      ) : null}
    </>
  )
}
