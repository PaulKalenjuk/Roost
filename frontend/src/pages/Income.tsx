import { useEffect, useMemo, useRef, useState } from 'react'
import {
  api,
  fetchList,
  money,
  type EarningsSummary,
  type ImportBatch,
  type IncomeCoverage,
  type IncomeEntry,
  type IncomeType,
  type Listing,
  type Owner,
  type Reservation,
} from '../api'
import { Alert, Field, Loading } from '../components'
import { useProperties } from '../store'

const FREQ_LABELS: Record<string, string> = {
  monthly: 'Monthly',
  quarterly: 'Quarterly',
  yearly: 'Yearly',
}

export default function Income() {
  const { selected } = useProperties()

  return (
    <>
      <h1>Income</h1>
      <p className="sub">
        Track every income source for a property — the Airbnb listing and anything
        else (storage rent, solar feed-in…). Each source is entered per month, quarter
        or year, so you can see which periods still need a figure.
      </p>

      {!selected ? (
        <Alert kind="info">Create a property first, on the Property setup page.</Alert>
      ) : (
        <>
          <IncomeSources />
          <AirbnbImport />
          <NightsAndReservations />
        </>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// Income sources (types), figures and coverage
// ---------------------------------------------------------------------------
function IncomeSources() {
  const { selected } = useProperties()
  const [owners, setOwners] = useState<Owner[]>([])
  const [listings, setListings] = useState<Listing[]>([])
  const [types, setTypes] = useState<IncomeType[]>([])
  const [typeId, setTypeId] = useState<number | null>(null)
  const [entries, setEntries] = useState<IncomeEntry[]>([])
  const [coverage, setCoverage] = useState<IncomeCoverage[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const [editingTypeId, setEditingTypeId] = useState<number | null>(null)
  const [newType, setNewType] = useState({
    name: '',
    owner: '',
    frequency: 'monthly',
    listing: '',
    coverage_start: '',
  })

  const [editingEntryId, setEditingEntryId] = useState<number | null>(null)
  const [entry, setEntry] = useState({
    income_type: '',
    period_start: '',
    period_end: '',
    amount: '',
    gross_amount: '',
    gst_amount: '',
    received: true,
    notes: '',
  })

  const loadTypes = async () => {
    if (!selected) return
    const list = await fetchList<IncomeType>(`/api/income-types/?property=${selected.id}`)
    setTypes(list)
    setTypeId((prev) => (prev && list.some((t) => t.id === prev) ? prev : (list[0]?.id ?? null)))
  }
  const loadEntries = async (id: number | null) => {
    if (!id) {
      setEntries([])
      return
    }
    setEntries(await fetchList<IncomeEntry>(`/api/income-entries/?income_type=${id}`))
  }
  const loadCoverage = async () => {
    if (!selected) return
    const data = await api<{ coverage: IncomeCoverage[] }>(
      `/api/income/coverage/?property=${selected.id}`,
    )
    setCoverage(data.coverage)
  }
  const loadOwners = async () => setOwners(await fetchList<Owner>('/api/owners/'))

  useEffect(() => {
    loadOwners()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    if (!selected) return
    fetchList<Listing>(`/api/listings/?property=${selected.id}`).then(setListings)
    loadTypes()
    loadCoverage()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id])
  useEffect(() => {
    loadEntries(typeId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typeId])

  const covFor = (id: number) => coverage.find((c) => c.income_type === id)
  const focused = covFor(typeId ?? -1)

  const resetTypeForm = () => {
    setEditingTypeId(null)
    setNewType({ name: '', owner: '', frequency: 'monthly', listing: '', coverage_start: '' })
  }
  const startEditType = (t: IncomeType) => {
    setEditingTypeId(t.id)
    setTypeId(t.id)
    setNewType({
      name: t.name,
      owner: t.owner ? String(t.owner) : '',
      frequency: t.frequency,
      listing: t.listing ? String(t.listing) : '',
      coverage_start: t.coverage_start ?? '',
    })
    setError('')
    setNotice(`Editing income source “${t.name}”.`)
  }
  const saveType = async () => {
    if (!selected || !newType.name.trim()) {
      setError('Give the income source a name.')
      return
    }
    setError('')
    const payload = {
      property: selected.id,
      name: newType.name.trim(),
      owner: newType.owner ? Number(newType.owner) : null,
      frequency: newType.frequency,
      listing: newType.listing ? Number(newType.listing) : null,
      coverage_start: newType.coverage_start || null,
    }
    try {
      if (editingTypeId) {
        await api(`/api/income-types/${editingTypeId}/`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
        setNotice('Income source updated.')
      } else {
        await api('/api/income-types/', { method: 'POST', body: JSON.stringify(payload) })
        setNotice('Income source added.')
      }
      resetTypeForm()
      await loadTypes()
      await loadCoverage()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }
  const deleteType = async (t: IncomeType) => {
    const n = covFor(t.id)?.entries ?? 0
    const extra = n ? ` and its ${n} figure(s)` : ''
    if (!window.confirm(`Delete the “${t.name}” income source${extra}?`)) return
    try {
      await api(`/api/income-types/${t.id}/`, { method: 'DELETE' })
      if (editingTypeId === t.id) resetTypeForm()
      await loadTypes()
      await loadCoverage()
      setNotice('Income source deleted.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  const resetEntryForm = () => {
    setEditingEntryId(null)
    setEntry({
      income_type: '',
      period_start: '',
      period_end: '',
      amount: '',
      gross_amount: '',
      gst_amount: '',
      received: true,
      notes: '',
    })
  }
  const prefillPeriod = (start: string, end: string) => {
    setEditingEntryId(null)
    setEntry({
      income_type: typeId ? String(typeId) : '',
      period_start: start,
      period_end: end,
      amount: '',
      gross_amount: '',
      gst_amount: '',
      received: true,
      notes: '',
    })
    setNotice(`Period prefilled (${start} → ${end}). Enter the amount, then save.`)
    entryFormRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  const startEditEntry = (e: IncomeEntry) => {
    setEditingEntryId(e.id)
    setTypeId(e.income_type)
    setEntry({
      income_type: String(e.income_type),
      period_start: e.period_start ?? '',
      period_end: e.period_end ?? '',
      amount: e.amount ?? '',
      gross_amount: e.gross_amount ?? '',
      gst_amount: e.gst_amount ?? '',
      received: e.received,
      notes: e.notes ?? '',
    })
    setError('')
    setNotice(`Editing the ${e.income_type_name} figure for ${e.period_start ?? '?'}.`)
    entryFormRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  const entryFormRef = useRef<HTMLFormElement>(null)

  const saveEntry = async (ev: React.FormEvent) => {
    ev.preventDefault()
    if (!entry.income_type) {
      setError('Pick an income source.')
      return
    }
    setError('')
    const payload = {
      income_type: Number(entry.income_type),
      period_start: entry.period_start || null,
      period_end: entry.period_end || null,
      amount: entry.amount || '0',
      gross_amount: entry.gross_amount === '' ? null : entry.gross_amount,
      gst_amount: entry.gst_amount || '0',
      received: entry.received,
      notes: entry.notes,
    }
    try {
      if (editingEntryId) {
        await api(`/api/income-entries/${editingEntryId}/`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
        setNotice('Income figure updated.')
      } else {
        await api('/api/income-entries/', { method: 'POST', body: JSON.stringify(payload) })
        setNotice('Income figure saved.')
      }
      resetEntryForm()
      await loadEntries(typeId)
      await loadCoverage()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }
  const deleteEntry = async (e: IncomeEntry) => {
    if (!window.confirm(`Delete the ${e.income_type_name} figure for ${e.period_start}?`)) return
    try {
      await api(`/api/income-entries/${e.id}/`, { method: 'DELETE' })
      if (editingEntryId === e.id) resetEntryForm()
      await loadEntries(typeId)
      await loadCoverage()
      setNotice('Income figure deleted.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  const saveCoverageStart = async (id: number, value: string) => {
    if (!value) {
      setError('Pick a date to track this income from.')
      return
    }
    try {
      await api(`/api/income-types/${id}/`, {
        method: 'PATCH',
        body: JSON.stringify({ coverage_start: value }),
      })
      await loadCoverage()
      setNotice('Coverage start saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }

  const amountTotal = entries.reduce((s, e) => s + Number(e.amount), 0)

  return (
    <>
      {notice ? <Alert kind="ok">{notice}</Alert> : null}
      {error ? <Alert kind="err">{error}</Alert> : null}

      <div className="panel">
        <h2>Income sources</h2>
        <p className="sub">
          Add each way the property earns (the Airbnb listing, storage rent, …), how
          often the money arrives, and — if you like — whose income it is.
        </p>
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Income</th>
                <th>Owner</th>
                <th>Frequency</th>
                <th>Listing</th>
                <th>Figures</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {types.map((t) => (
                <tr
                  key={t.id}
                  className={t.id === typeId ? 'selected-row' : ''}
                  style={{ cursor: 'pointer' }}
                  onClick={() => setTypeId(t.id)}
                >
                  <td>{t.name}</td>
                  <td>{t.owner_name || '—'}</td>
                  <td>{FREQ_LABELS[t.frequency] ?? t.frequency}</td>
                  <td>{t.listing_name || '—'}</td>
                  <td>
                    <IncomeCoverageCell cov={covFor(t.id)} />
                  </td>
                  <td>
                    <div className="row">
                      <button type="button" className="ghost small" onClick={() => startEditType(t)}>
                        Edit
                      </button>
                      <button type="button" className="ghost small" onClick={() => deleteType(t)}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {types.length === 0 ? (
                <tr>
                  <td colSpan={6} className="muted">
                    No income sources yet — add one below (Airbnb is added automatically
                    when you import an earnings report).
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <div className="row" style={{ marginTop: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div>
            <label>Name</label>
            <input
              value={newType.name}
              onChange={(e) => setNewType({ ...newType, name: e.target.value })}
              placeholder="e.g. Storage rent"
            />
          </div>
          <div>
            <label>Owner</label>
            <select value={newType.owner} onChange={(e) => setNewType({ ...newType, owner: e.target.value })}>
              <option value="">— shared by ownership —</option>
              {owners.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Frequency</label>
            <select
              value={newType.frequency}
              onChange={(e) => setNewType({ ...newType, frequency: e.target.value })}
            >
              <option value="monthly">Monthly</option>
              <option value="quarterly">Quarterly</option>
              <option value="yearly">Yearly</option>
            </select>
          </div>
          <div>
            <label>Listing (optional)</label>
            <select value={newType.listing} onChange={(e) => setNewType({ ...newType, listing: e.target.value })}>
              <option value="">— none —</option>
              {listings.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Track from</label>
            <input
              type="date"
              value={newType.coverage_start}
              onChange={(e) => setNewType({ ...newType, coverage_start: e.target.value })}
            />
          </div>
          <button type="button" className="ghost small" onClick={saveType}>
            {editingTypeId ? 'Save changes' : '+ Add income source'}
          </button>
          {editingTypeId ? (
            <button type="button" className="ghost small" onClick={resetTypeForm}>
              Cancel
            </button>
          ) : null}
        </div>
        <div className="hint" style={{ marginTop: 8 }}>
          “Track from” defaults to the property's purchase date. It drives the list of
          periods that still need a figure.
        </div>
      </div>

      {focused ? (
        <div className="panel">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <h2 style={{ margin: 0 }}>Missing figures — {focused.name}</h2>
            <span className="muted">
              {focused.frequency_label} · {focused.entries} figure(s)
            </span>
          </div>

          {focused.computable ? (
            <>
              <div className="row" style={{ marginTop: 12, gap: 16 }}>
                <span className={`badge ${focused.missing_periods ? 'warn' : 'ok'}`}>
                  {focused.coverage_pct.toFixed(0)}% of periods entered
                </span>
                <span className="muted">
                  {focused.entered_periods} of {focused.total_periods} periods
                </span>
                {focused.missing_periods ? (
                  <span className="badge err">{focused.missing_periods} still to enter</span>
                ) : null}
                {focused.entries_missing_period ? (
                  <span className="badge warn">
                    {focused.entries_missing_period} figure(s) without a period
                  </span>
                ) : null}
              </div>

              {focused.missing.length ? (
                <>
                  <h3>Periods yet to be entered</h3>
                  <div className="tablewrap">
                    <table>
                      <thead>
                        <tr>
                          <th>From</th>
                          <th>To</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {focused.missing.map((g, i) => (
                          <tr key={i}>
                            <td>{g.start}</td>
                            <td>{g.end}</td>
                            <td>
                              <button
                                type="button"
                                className="ghost small"
                                onClick={() => prefillPeriod(g.start, g.end)}
                              >
                                Enter figure
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : (
                <p className="muted" style={{ marginTop: 12 }}>
                  Every period since {focused.start} has a figure.
                </p>
              )}
            </>
          ) : (
            <div>
              <Alert kind="info">{focused.reason}</Alert>
              <div className="row">
                <div>
                  <label>Track {focused.name} from</label>
                  <input
                    type="date"
                    value={newType.coverage_start}
                    onChange={(e) => setNewType({ ...newType, coverage_start: e.target.value })}
                  />
                </div>
                <button
                  type="button"
                  className="small"
                  onClick={() => saveCoverageStart(focused.income_type, newType.coverage_start)}
                >
                  Save start date
                </button>
              </div>
            </div>
          )}
        </div>
      ) : null}

      <form className="panel" onSubmit={saveEntry} ref={entryFormRef}>
        <h2>{editingEntryId ? 'Edit income figure' : 'Add an income figure'}</h2>
        <div className="grid">
          <Field label="Income source">
            <select
              value={entry.income_type}
              onChange={(e) => setEntry({ ...entry, income_type: e.target.value })}
            >
              <option value="">— select —</option>
              {types.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({FREQ_LABELS[t.frequency]})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Period start">
            <input
              type="date"
              value={entry.period_start}
              onChange={(e) => setEntry({ ...entry, period_start: e.target.value })}
            />
          </Field>
          <Field label="Period end">
            <input
              type="date"
              value={entry.period_end}
              onChange={(e) => setEntry({ ...entry, period_end: e.target.value })}
            />
          </Field>
          <Field label="Amount (net)">
            <input
              type="number"
              step="0.01"
              value={entry.amount}
              onChange={(e) => setEntry({ ...entry, amount: e.target.value })}
            />
          </Field>
          <Field label="Gross (optional)">
            <input
              type="number"
              step="0.01"
              value={entry.gross_amount}
              onChange={(e) => setEntry({ ...entry, gross_amount: e.target.value })}
            />
          </Field>
          <Field label="GST collected">
            <input
              type="number"
              step="0.01"
              value={entry.gst_amount}
              onChange={(e) => setEntry({ ...entry, gst_amount: e.target.value })}
            />
          </Field>
          <Field label="Received">
            <select
              value={String(entry.received)}
              onChange={(e) => setEntry({ ...entry, received: e.target.value === 'true' })}
            >
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </Field>
          <Field label="Notes">
            <input value={entry.notes} onChange={(e) => setEntry({ ...entry, notes: e.target.value })} />
          </Field>
        </div>
        <div style={{ height: 12 }} />
        <div className="row">
          <button disabled={!entry.income_type}>
            {editingEntryId ? 'Save changes' : 'Save figure'}
          </button>
          {editingEntryId ? (
            <button type="button" className="ghost" onClick={resetEntryForm}>
              Cancel
            </button>
          ) : null}
        </div>
      </form>

      <div className="panel">
        <h2>Income figures — {types.find((t) => t.id === typeId)?.name ?? 'all'}</h2>
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Period</th>
                <th className="num">Amount</th>
                <th className="num">Gross</th>
                <th className="num">GST</th>
                <th>Received</th>
                <th>Source</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id} className={e.id === editingEntryId ? 'selected-row' : ''}>
                  <td>
                    {e.period_start ?? '?'} → {e.period_end ?? '?'}
                  </td>
                  <td className="num">{money(e.amount)}</td>
                  <td className="num">{e.gross_amount ? money(e.gross_amount) : '—'}</td>
                  <td className="num">{money(e.gst_amount)}</td>
                  <td>{e.received ? 'Yes' : 'No'}</td>
                  <td>{e.source === 'airbnb_pdf' ? 'Airbnb import' : 'Manual'}</td>
                  <td>
                    <div className="row">
                      <button type="button" className="ghost small" onClick={() => startEditEntry(e)}>
                        Edit
                      </button>
                      <button type="button" className="ghost small" onClick={() => deleteEntry(e)}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {entries.length === 0 ? (
                <tr>
                  <td colSpan={7} className="muted">
                    No figures for this source yet.
                  </td>
                </tr>
              ) : null}
            </tbody>
            {entries.length ? (
              <tfoot>
                <tr>
                  <td>Total</td>
                  <td className="num">{money(amountTotal)}</td>
                  <td colSpan={5} />
                </tr>
              </tfoot>
            ) : null}
          </table>
        </div>
      </div>
    </>
  )
}

function IncomeCoverageCell({ cov }: { cov?: IncomeCoverage }) {
  if (!cov) return <span className="muted">—</span>
  if (!cov.computable) {
    return (
      <span className="badge warn" title={cov.reason ?? ''}>
        set dates
      </span>
    )
  }
  if (cov.missing_periods === 0) {
    return <span className="badge ok">{cov.coverage_pct.toFixed(0)}%</span>
  }
  return (
    <span
      className="badge warn"
      title={`${cov.entered_periods} of ${cov.total_periods} periods entered`}
    >
      {cov.entered_periods}/{cov.total_periods} · {cov.missing_periods} to enter
    </span>
  )
}

// ---------------------------------------------------------------------------
// Airbnb earnings-report import
// ---------------------------------------------------------------------------
function AirbnbImport() {
  const { selected } = useProperties()
  const [listings, setListings] = useState<Listing[]>([])
  const [listingId, setListingId] = useState<number | null>(null)
  const [imports, setImports] = useState<ImportBatch[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [batch, setBatch] = useState<ImportBatch | null>(null)
  const [importing, setImporting] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = async (id: number) => {
    setLoading(true)
    try {
      setImports(await fetchList<ImportBatch>(`/api/imports/?listing=${id}&source=airbnb_pdf`))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!selected) {
      setListings([])
      setListingId(null)
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

  return (
    <div className="panel">
      <h2>Import Airbnb earnings report (PDF)</h2>
      <p className="sub">
        The report's monthly totals are imported and also placed on the Airbnb income
        source above, where you can see any months still to enter. Re-importing
        overwrites the same months. The PDF is kept with the import.
      </p>
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
      <div className="hint">Airbnb → Payments &amp; payouts → Earnings → Download report.</div>
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

      {loading ? <Loading what="income" /> : null}

      <h3>Import history</h3>
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
                    <span className="muted">{b.filename || '—'} (no file kept)</span>
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
  )
}

// ---------------------------------------------------------------------------
// Nights booked + reservations
// ---------------------------------------------------------------------------
function NightsAndReservations() {
  const { selected } = useProperties()
  const [listings, setListings] = useState<Listing[]>([])
  const [listingId, setListingId] = useState<number | null>(null)
  const [reservations, setReservations] = useState<Reservation[]>([])
  const [summaries, setSummaries] = useState<EarningsSummary[]>([])
  const [nightsDraft, setNightsDraft] = useState<Record<number, string>>({})
  const [manualFy, setManualFy] = useState('')
  const [manualNights, setManualNights] = useState('')
  const [manualAvg, setManualAvg] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

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

  const load = async (id: number) => {
    const [r, s] = await Promise.all([
      fetchList<Reservation>(`/api/reservations/?listing=${id}`),
      fetchList<EarningsSummary>(`/api/earnings-summaries/?listing=${id}`),
    ])
    setReservations(r)
    setSummaries(s)
  }

  useEffect(() => {
    if (!selected) {
      setListingId(null)
      return
    }
    fetchList<Listing>(`/api/listings/?property=${selected.id}`).then((ls) => {
      setListings(ls)
      setListingId(ls[0]?.id ?? null)
    })
  }, [selected?.id])

  useEffect(() => {
    if (listingId) load(listingId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listingId])

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
    if (!window.confirm(`Delete the nights record for ${summary.financial_year}?`)) return
    try {
      await api(`/api/earnings-summaries/${summary.id}/`, { method: 'DELETE' })
      if (listingId) await load(listingId)
      setNotice('Nights record deleted.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  const byFy = useMemo(() => {
    const map = new Map<string, EarningsSummary[]>()
    for (const summary of summaries) {
      const key = summary.financial_year || '—'
      const existing = map.get(key)
      if (existing) existing.push(summary)
      else map.set(key, [summary])
    }
    return map
  }, [summaries])

  return (
    <div className="panel">
      <h2>Nights booked &amp; reservations</h2>
      {notice ? <Alert kind="ok">{notice}</Alert> : null}
      {error ? <Alert kind="err">{error}</Alert> : null}

      <div className="row" style={{ marginBottom: 8 }}>
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
      </div>

      <h3>Nights booked per financial year</h3>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Financial year</th>
              <th>Period</th>
              <th className="num">Nights booked</th>
              <th className="num">Avg night stay</th>
              <th />
            </tr>
          </thead>
          {[...byFy.entries()].map(([fy, group]) => (
            <tbody key={fy}>
              {group.map((s) => (
                <tr key={s.id}>
                  <td>{fy}</td>
                  <td className="muted">
                    {s.period_start ?? '?'} → {s.period_end ?? '?'}
                  </td>
                  <td className="num">
                    <input
                      type="number"
                      min="0"
                      style={{ width: 90, display: 'inline-block' }}
                      value={nightsDraft[s.id] ?? s.nights_booked ?? ''}
                      onChange={(e) => setNightsDraft({ ...nightsDraft, [s.id]: e.target.value })}
                    />
                    {nightsDraft[s.id] !== undefined ? (
                      <button type="button" className="ghost small" onClick={() => saveNights(s.id)}>
                        Save
                      </button>
                    ) : null}
                    <button type="button" className="ghost small" onClick={() => deleteSummary(s)}>
                      Delete
                    </button>
                  </td>
                  <td className="num">{s.avg_night_stay ?? '—'}</td>
                  <td />
                </tr>
              ))}
            </tbody>
          ))}
          {summaries.length === 0 ? (
            <tbody>
              <tr>
                <td colSpan={5} className="muted">
                  Nothing yet — import an earnings report, or add a year below.
                </td>
              </tr>
            </tbody>
          ) : null}
        </table>
      </div>

      <div className="row" style={{ marginTop: 12 }}>
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
        <button type="button" className="ghost small" onClick={saveManualNights} disabled={!listingId}>
          Save nights
        </button>
      </div>

      <h3>Reservations (from CSV import)</h3>
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
                  No reservations yet (import a transaction CSV via the Django admin).
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  )
}
