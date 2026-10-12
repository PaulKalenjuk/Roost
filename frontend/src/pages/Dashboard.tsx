import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Coverage, type CoverageSegment, type IncomeCoverage } from '../api'
import { Alert, CoverageBar, Loading } from '../components'
import { useProperties } from '../store'

export default function Dashboard() {
  const { selected } = useProperties()
  const navigate = useNavigate()
  const [utilityCov, setUtilityCov] = useState<Coverage[]>([])
  const [incomeCov, setIncomeCov] = useState<IncomeCoverage[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!selected) {
      setUtilityCov([])
      setIncomeCov([])
      return
    }
    let cancelled = false
    setLoading(true)
    setError('')
    Promise.all([
      api<{ coverage: Coverage[] }>(`/api/utilities/coverage/?property=${selected.id}`),
      api<{ coverage: IncomeCoverage[] }>(`/api/income/coverage/?property=${selected.id}`),
    ])
      .then(([u, i]) => {
        if (cancelled) return
        setUtilityCov(u.coverage)
        setIncomeCov(i.coverage)
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [selected?.id])

  const utilitiesNeeding = utilityCov.filter(
    (c) => c.computable && (c.gaps.length > 0 || c.bills_missing_period > 0),
  )
  const incomesNeeding = incomeCov.filter(
    (c) => c.computable && (c.missing_periods > 0 || c.entries_missing_period > 0),
  )

  const openUtility = (id: number, gap?: CoverageSegment) => {
    const params = new URLSearchParams({ tab: 'utilities', utility_type: String(id) })
    if (gap) {
      params.set('from', gap.start)
      params.set('to', gap.end)
    }
    navigate(`/expenses?${params.toString()}`)
  }
  const openIncome = (id: number, gap?: CoverageSegment) => {
    const params = new URLSearchParams({ income_type: String(id) })
    if (gap) {
      params.set('from', gap.start)
      params.set('to', gap.end)
    }
    navigate(`/income?${params.toString()}`)
  }

  const nothingMissing =
    !loading && utilitiesNeeding.length === 0 && incomesNeeding.length === 0 &&
    (utilityCov.length > 0 || incomeCov.length > 0)

  return (
    <>
      <h1>Dashboard</h1>
      <p className="sub">
        Everything still to enter — utility bills and income figures with a gap in
        the timeline. Click a red block on a bar to add that period's amount.
      </p>

      {!selected ? <Alert kind="info">Create a property first.</Alert> : null}
      {error ? <Alert kind="err">{error}</Alert> : null}
      {loading ? <Loading what="outstanding items" /> : null}

      {selected ? (
        <>
          <div className="panel">
            <h2>Utilities needing a bill</h2>
            <p className="sub">
              {utilitiesNeeding.length
                ? `${utilitiesNeeding.length} utility type(s) with coverage gaps.`
                : 'Nothing outstanding.'}
            </p>
            {utilitiesNeeding.map((c) => (
              <div key={c.utility_type} className="card" style={{ marginBottom: 14 }}>
                <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <h3 style={{ margin: 0 }}>{c.name}</h3>
                  <span className="muted">
                    {c.frequency_label} · {c.covered_days}/{c.total_days} days covered
                  </span>
                </div>
                <div className="row" style={{ gap: 12, marginTop: 6 }}>
                  <span className={`badge ${c.gaps.length ? 'err' : 'ok'}`}>
                    {c.gaps.length} gap{c.gaps.length === 1 ? '' : 's'} · {c.gap_days} days
                  </span>
                  {c.bills_missing_period ? (
                    <span className="badge warn">{c.bills_missing_period} bill(s) without a period</span>
                  ) : null}
                </div>
                <CoverageBar coverage={c} onGapClick={(gap) => openUtility(c.utility_type, gap)} />
                {c.gaps.length ? (
                  <div className="tablewrap" style={{ marginTop: 10 }}>
                    <table>
                      <thead>
                        <tr>
                          <th>Missing period</th>
                          <th className="num">Days</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {c.gaps.map((g, i) => (
                          <tr key={i}>
                            <td>
                              {g.start} → {g.end}
                            </td>
                            <td className="num">{g.days}</td>
                            <td>
                              <button
                                type="button"
                                className="small"
                                onClick={() =>
                                  openUtility(c.utility_type, {
                                    kind: 'gap',
                                    start: g.start,
                                    end: g.end,
                                    days: g.days,
                                    width_pct: 0,
                                  })
                                }
                              >
                                Enter bill
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
                {c.bills_missing_period ? (
                  <button
                    type="button"
                    className="ghost small"
                    style={{ marginTop: 8 }}
                    onClick={() => openUtility(c.utility_type)}
                  >
                    Fix bills without a period →
                  </button>
                ) : null}
              </div>
            ))}
            {utilityCov.length === 0 ? (
              <p className="muted">No utility types yet — add them under Expenses → Utilities.</p>
            ) : null}
          </div>

          <div className="panel">
            <h2>Income needing a figure</h2>
            <p className="sub">
              {incomesNeeding.length
                ? `${incomesNeeding.length} income source(s) with periods still to enter.`
                : 'Nothing outstanding.'}
            </p>
            {incomesNeeding.map((c) => (
              <div key={c.income_type} className="card" style={{ marginBottom: 14 }}>
                <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <h3 style={{ margin: 0 }}>
                    {c.name}
                    {c.owner_name ? <span className="muted"> · {c.owner_name}</span> : null}
                  </h3>
                  <span className="muted">
                    {c.frequency_label} · {c.entered_periods}/{c.total_periods} periods entered
                  </span>
                </div>
                <div className="row" style={{ gap: 12, marginTop: 6 }}>
                  <span className={`badge ${c.missing_periods ? 'err' : 'ok'}`}>
                    {c.missing_periods} to enter
                  </span>
                  {c.entries_missing_period ? (
                    <span className="badge warn">
                      {c.entries_missing_period} figure(s) without a period
                    </span>
                  ) : null}
                </div>
                <CoverageBar coverage={c} onGapClick={(gap) => openIncome(c.income_type, gap)} />
                {c.current_period ? (
                  <p className="muted" style={{ marginTop: 4 }}>
                    Current {c.frequency_label.toLowerCase()} period ({c.current_period.start} →{' '}
                    {c.current_period.end}) is in progress and isn't counted.
                  </p>
                ) : null}
                {c.missing.length ? (
                  <div className="tablewrap" style={{ marginTop: 10 }}>
                    <table>
                      <thead>
                        <tr>
                          <th>Missing period</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {c.missing.map((g, i) => (
                          <tr key={i}>
                            <td>
                              {g.start} → {g.end}
                            </td>
                            <td>
                              <button
                                type="button"
                                className="small"
                                onClick={() =>
                                  openIncome(c.income_type, {
                                    kind: 'gap',
                                    start: g.start,
                                    end: g.end,
                                    days: 0,
                                    width_pct: 0,
                                  })
                                }
                              >
                                Enter figure
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </div>
            ))}
            {incomeCov.length === 0 ? (
              <p className="muted">No income sources yet — add them under Income.</p>
            ) : null}
          </div>

          {nothingMissing ? (
            <Alert kind="ok">All caught up — no missing utility bills or income figures. 🎉</Alert>
          ) : null}
        </>
      ) : null}
    </>
  )
}
