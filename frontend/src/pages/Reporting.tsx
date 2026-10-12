import { useEffect, useState } from 'react'
import { api, fetchList, money, pct, type Owner, type Ownership } from '../api'
import { Alert, Loading } from '../components'
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
interface IncomeTypeRow {
  income_type: number
  name: string
  owner_name: string | null
  frequency_label: string
  gross: Num
  amount: Num
  gst: Num
}
interface Report {
  property: string
  financial_year: string
  period_label: string
  period_type: 'year' | 'quarter' | 'month'
  period: [string, string]
  let_share: Num
  gst_registered: boolean
  income: {
    gross_earnings: Num
    service_fees: Num
    tax_withheld: Num
    total_earnings: Num
    gst_collected: Num
    nights_booked: number
    months: MonthRow[]
    by_type: IncomeTypeRow[]
  }
  expenses: {
    total_amount: Num
    total_deductible: Num
    gst_total: Num
    gst_claimable: Num
    by_category: Record<string, { amount: Num; deductible: Num }>
    notes?: string[]
    items: Array<{ date: string; category: string; description: string; amount: Num; apportionment: string; share: Num; deductible_amount: Num; detail?: string; working?: string }>
  }
  depreciation: {
    total_deduction: Num
    prorated?: boolean
    lines: Array<{ asset: string; method: string; opening_value: Num; business_use_pct: Num; deduction: Num; closing_value: Num; days_held: number | null; working?: string }>
  }
  gst: { collected: Num; credits: Num; net: Num } | null
  net_rental_result: Num
}
interface OwnerView {
  owner: string
  owner_email: string
  share: Num
  income: { gross_earnings: Num; total_earnings: Num; gst_collected: Num }
  expenses_by_category: Record<string, { deductible: Num }>
  expenses_deductible: Num
  depreciation: Num
  net_rental_result: Num
}
interface ReportResponse {
  report: Report
  owners?: OwnerView[]
  period_type: 'year' | 'quarter' | 'month'
  period_value: string
  fy_options: string[]
  quarter_options: string[]
  month_options: string[]
  default_fy: string
  default_quarter: string
  default_month: string
}

type PeriodType = 'year' | 'quarter' | 'month'

interface OwnerOption {
  id: number
  name: string
  bas_frequency: PeriodType
}

export default function Reporting() {
  const { selected } = useProperties()
  const [periodType, setPeriodType] = useState<PeriodType | ''>('')
  const [fy, setFy] = useState('')
  const [quarter, setQuarter] = useState('')
  const [month, setMonth] = useState('')
  const [fyOptions, setFyOptions] = useState<string[]>([])
  const [quarterOptions, setQuarterOptions] = useState<string[]>([])
  const [monthOptions, setMonthOptions] = useState<string[]>([])
  const [ownerOptions, setOwnerOptions] = useState<OwnerOption[]>([])
  const [ownerId, setOwnerId] = useState<number | null>(null)
  const [includeOwners, setIncludeOwners] = useState(true)
  const [recompute, setRecompute] = useState(false)
  const [working, setWorking] = useState(true)
  const [data, setData] = useState<ReportResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const clearPeriodValue = () => {
    setFy('')
    setQuarter('')
    setMonth('')
  }

  // When the property changes, load its owners and default the reporting period
  // to the first owner's BAS frequency (monthly / quarterly / yearly).
  useEffect(() => {
    if (!selected) {
      setOwnerOptions([])
      setOwnerId(null)
      setPeriodType('')
      return
    }
    let cancelled = false
    Promise.all([
      fetchList<Ownership>(`/api/ownerships/?property=${selected.id}`),
      fetchList<Owner>('/api/owners/'),
    ]).then(([ownerships, owners]) => {
      if (cancelled) return
      const byId = new Map(owners.map((o) => [o.id, o]))
      const list: OwnerOption[] = ownerships.map((w) => ({
        id: w.owner,
        name: w.owner_name,
        bas_frequency: (byId.get(w.owner)?.bas_frequency ?? 'yearly') as PeriodType,
      }))
      setOwnerOptions(list)
      const first = list[0]
      setOwnerId(first?.id ?? null)
      setPeriodType(first?.bas_frequency ?? 'year')
      clearPeriodValue()
    })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id])

  useEffect(() => {
    if (!selected || !periodType) {
      setData(null)
      return
    }
    let cancelled = false
    const go = async () => {
      setLoading(true)
      setError('')
      try {
        const params = new URLSearchParams({ property: String(selected.id), period: periodType })
        if (periodType === 'year' && fy) params.set('fy', fy)
        if (periodType === 'quarter' && quarter) params.set('quarter', quarter)
        if (periodType === 'month' && month) params.set('month', month)
        if (includeOwners) params.set('owners', '1')
        if (recompute) params.set('recompute', '1')
        if (working) params.set('working', '1')
        const result = await api<ReportResponse>(`/api/reports/fy/?${params}`)
        if (cancelled) return
        setData(result)
        setFyOptions(result.fy_options)
        setQuarterOptions(result.quarter_options ?? [])
        setMonthOptions(result.month_options ?? [])
        // Adopt the period the server resolved (its default when we sent none).
        if (periodType === 'year' && !fy) setFy(result.period_value)
        if (periodType === 'quarter' && !quarter) setQuarter(result.period_value)
        if (periodType === 'month' && !month) setMonth(result.period_value)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Report failed')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    go()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, periodType, fy, quarter, month])

  const chooseOwner = (id: number | null) => {
    setOwnerId(id)
    const o = ownerOptions.find((x) => x.id === id)
    if (o) {
      setPeriodType(o.bas_frequency)
      clearPeriodValue()
    }
  }

  const periodQuery = () => {
    const params = new URLSearchParams({ property: String(selected?.id ?? ''), period: periodType || 'year' })
    if (periodType === 'year' && fy) params.set('fy', fy)
    if (periodType === 'quarter' && quarter) params.set('quarter', quarter)
    if (periodType === 'month' && month) params.set('month', month)
    return params
  }

  const exportReceipts = () => {
    if (!selected) return
    const params = periodQuery()
    if (includeOwners) params.set('owners', '1')
    if (working) params.set('working', '1')
    window.open(`/api/reports/fy/receipts/?${params.toString()}`, '_blank')
  }

  const exportPdf = () => {
    if (!selected) return
    const params = periodQuery()
    if (includeOwners) params.set('owners', '1')
    if (working) params.set('working', '1')
    if (recompute) params.set('recompute', '1')
    window.open(`/api/reports/fy/pdf/?${params.toString()}`, '_blank')
  }

  const report = data?.report
  const options =
    periodType === 'month' ? monthOptions : periodType === 'quarter' ? quarterOptions : fyOptions
  const periodValue = periodType === 'month' ? month : periodType === 'quarter' ? quarter : fy
  const selectedOwner = ownerOptions.find((o) => o.id === ownerId)

  return (
    <>
      <h1>Reporting</h1>
      <p className="sub">
        Report by month, quarter (BAS periods: Jul–Sep, Oct–Dec, Jan–Mar, Apr–Jun) or
        financial year (1 July – 30 June), with the maths shown and an optional
        per-owner split. The period defaults to the selected owner's BAS frequency.
      </p>

      {!selected ? <Alert kind="info">Create a property first.</Alert> : null}

      <div className="panel">
        <div className="row">
          {ownerOptions.length ? (
            <div>
              <label>Owner (BAS default)</label>
              <select
                value={ownerId ?? ''}
                onChange={(e) => chooseOwner(e.target.value ? Number(e.target.value) : null)}
              >
                {ownerOptions.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
          <div>
            <label>Report by</label>
            <select
              value={periodType}
              onChange={(e) => {
                setPeriodType(e.target.value as PeriodType)
                clearPeriodValue()
              }}
            >
              <option value="month">Monthly</option>
              <option value="quarter">Quarter</option>
              <option value="year">Financial year</option>
            </select>
          </div>
          <div>
            <label>
              {periodType === 'month'
                ? 'Month'
                : periodType === 'quarter'
                  ? 'Quarter'
                  : 'Financial year'}
            </label>
            <select
              value={periodValue}
              onChange={(e) =>
                periodType === 'month'
                  ? setMonth(e.target.value)
                  : periodType === 'quarter'
                    ? setQuarter(e.target.value)
                    : setFy(e.target.value)
              }
            >
              {(options.length ? options : ['']).map((o) => (
                <option key={o} value={o}>
                  {o || 'default'}
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
          <div>
            <label>Show the working</label>
            <select value={String(working)} onChange={(e) => setWorking(e.target.value === 'true')}>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </div>
          <button className="ghost" onClick={exportPdf} disabled={!selected}>
            Export PDF
          </button>
          <button className="ghost" onClick={exportReceipts} disabled={!selected}>
            Export receipts (ZIP)
          </button>
        </div>
        <div className="hint">
          {selectedOwner
            ? `${selectedOwner.name}'s BAS frequency is ${selectedOwner.bas_frequency} — the period defaults to the previous one.`
            : 'Defaults: previous month (monthly), previous quarter (quarterly), previous financial year (yearly).'}
        </div>
      </div>

      {error ? <Alert kind="err">{error}</Alert> : null}
      {loading ? <Loading what="report" /> : null}

      {report ? (
        <>
          <div className="panel">
            <h2>
              {report.property} — {report.period_label ?? report.financial_year}
            </h2>
            <p className="muted">
              {report.period?.[0]} → {report.period?.[1]} · Let share {pct(report.let_share)} · GST{' '}
              {report.gst_registered ? 'registered' : 'not registered'}
            </p>

            <h3>Income</h3>
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
            </div>

            {report.income.by_type?.length ? (
              <>
                <h3>Income by source</h3>
                <div className="tablewrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Income source</th>
                        <th>Owner</th>
                        <th>Frequency</th>
                        <th className="num">Net</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.income.by_type.map((t) => (
                        <tr key={t.income_type}>
                          <td>{t.name}</td>
                          <td>{t.owner_name || '—'}</td>
                          <td>{t.frequency_label}</td>
                          <td className="num">{money(t.amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}

            <h3>Expenses (claimable portion)</h3>
            <div className="tablewrap">
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
            </div>

            {working && report.expenses.items.length ? (
              <>
                <h3>Expenses — working</h3>
                <div className="tablewrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Category</th>
                        <th>Description</th>
                        <th className="num">Amount</th>
                        <th>Basis</th>
                        <th className="num">Claimable</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.expenses.items.map((item, i) => (
                        <tr key={i}>
                          <td>{item.date}</td>
                          <td>{item.category}</td>
                          <td>{item.description || '—'}</td>
                          <td className="num">{money(item.amount)}</td>
                          <td>
                            {item.apportionment}
                            {item.working ? <div className="working">{item.working}</div> : null}
                          </td>
                          <td className="num">{money(item.deductible_amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}

            {report.expenses.notes?.length ? (
              <p className="muted" style={{ marginTop: 8 }}>
                Apportionment: {report.expenses.notes.join('; ')}
              </p>
            ) : null}

            <h3>Depreciation</h3>
            <div className="tablewrap">
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
                      <td>
                        {l.asset}
                        {working && l.working ? <div className="working">{l.working}</div> : null}
                      </td>
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
                {report.depreciation.lines.length ? (
                  <tfoot>
                    <tr>
                      <td>Total depreciation</td>
                      <td></td>
                      <td></td>
                      <td></td>
                      <td className="num">{money(report.depreciation.total_deduction)}</td>
                    </tr>
                  </tfoot>
                ) : null}
              </table>
            </div>
            {report.depreciation.prorated ? (
              <p className="muted">
                Depreciation is prorated to the period from the financial-year schedule.
              </p>
            ) : null}

            {report.gst ? (
              <>
                <h3>GST (BAS summary)</h3>
                <div className="tablewrap">
                  <table>
                    <tbody>
                      <tr>
                        <td>GST collected on income</td>
                        <td className="num">{money(report.gst.collected)}</td>
                      </tr>
                      <tr>
                        <td>GST credits on claimable expenses</td>
                        <td className="num">{money(report.gst.credits)}</td>
                      </tr>
                      <tr>
                        <td>
                          <strong>Net GST</strong>
                        </td>
                        <td className="num">
                          <strong>{money(report.gst.net)}</strong>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}

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
                  <div className="tablewrap">
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
                </div>
              ))}
            </div>
          ) : null}
        </>
      ) : null}
    </>
  )
}
