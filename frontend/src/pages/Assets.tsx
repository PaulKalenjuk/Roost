import { useEffect, useRef, useState } from 'react'
import { api, fetchList, money, pct, type Asset, type AssetExtraction } from '../api'
import { Alert, Field } from '../components'
import { useProperties } from '../store'

export default function Assets() {
  const { selected } = useProperties()
  const [items, setItems] = useState<Asset[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [form, setForm] = useState({
    name: '',
    kind: 'plant_equipment',
    purchase_date: new Date().toISOString().slice(0, 10),
    cost: '',
    effective_life_years: '',
    method: 'diminishing_value',
    business_use_pct: '',
    low_value_pool: false,
  })
  const assetFileRef = useRef<HTMLInputElement>(null)
  const [reading, setReading] = useState(false)
  const [estimated, setEstimated] = useState(false)

  const load = async () => {
    if (!selected) return
    setItems(await fetchList<Asset>(`/api/assets/?property=${selected.id}`))
  }
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id])

  useEffect(() => {
    if (selected) {
      setForm((f) => ({
        ...f,
        method: selected.default_depreciation_method,
        business_use_pct: selected.let_share ?? '',
      }))
    }
  }, [selected?.id, selected?.default_depreciation_method, selected?.let_share])

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selected) return
    setError('')
    setNotice('')
    if (!form.name.trim() || !form.cost) {
      setError('Name and cost are required.')
      return
    }
    try {
      const created = await api<Asset>('/api/assets/', {
        method: 'POST',
        body: JSON.stringify({
          property: selected.id,
          name: form.name,
          kind: form.kind,
          purchase_date: form.purchase_date,
          cost: form.cost,
          effective_life_years: form.effective_life_years || null,
          method: form.method,
          business_use_pct: form.business_use_pct || '1',
          low_value_pool: form.low_value_pool,
          effective_life_is_estimate: estimated,
        }),
      })
      await api(`/api/assets/${created.id}/recompute/`, { method: 'POST' })
      const file = assetFileRef.current?.files?.[0]
      if (file) {
        const body = new FormData()
        body.append('file', file)
        body.append('property', String(selected.id))
        body.append('asset', String(created.id))
        body.append('original_name', file.name)
        await api('/api/receipts/', { method: 'POST', body })
        if (assetFileRef.current) assetFileRef.current.value = ''
        setNotice('Asset added with its receipt attached, and the depreciation schedule built.')
      } else {
        setNotice('Asset added and depreciation schedule built.')
      }
      setForm({ ...form, name: '', cost: '', effective_life_years: '' })
      setEstimated(false)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }

  const readReceipt = async () => {
    const file = assetFileRef.current?.files?.[0]
    if (!file) {
      setError('Choose the receipt file first.')
      return
    }
    setError('')
    setNotice('')
    setReading(true)
    const body = new FormData()
    body.append('file', file)
    try {
      const data = await api<AssetExtraction>('/api/assets/extract/', { method: 'POST', body })
      if (data.needs_ocr) {
        setError(data.message ?? 'This receipt has no text layer — enter the details manually.')
        return
      }
      setForm((f) => ({
        ...f,
        name: data.name ?? f.name,
        cost: data.cost ?? f.cost,
        purchase_date: data.purchase_date ?? f.purchase_date,
        effective_life_years: data.effective_life_years ?? f.effective_life_years,
        kind: data.asset_kind ?? f.kind,
        method: data.suggested_method ?? f.method,
      }))
      setEstimated(Boolean(data.effective_life_is_estimate))
      setNotice(`Read with ${data.extracted_by ?? 'AI'} — check the values, then Add asset.`)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Extraction failed'
      setError(`${message} — you can still fill the fields in manually.`)
    } finally {
      setReading(false)
    }
  }

  const recomputeAll = async () => {
    if (!selected) return
    await api('/api/assets/recompute/', { method: 'POST', body: JSON.stringify({ property: selected.id }) })
    setNotice('Depreciation rebuilt for all assets.')
    await load()
  }

  return (
    <>
      <h1>Depreciating assets</h1>
      <p className="sub">
        Register plant &amp; equipment and building works. Depreciation is calculated
        per financial year (prime cost or diminishing value), pro-rated for days held
        and scaled by business use. Attach the purchase receipt (PDF or image) when you
        add an asset, or later from the assets table.
      </p>

      {!selected ? <Alert kind="info">Create a property first.</Alert> : null}
      {notice ? <Alert kind="ok">{notice}</Alert> : null}
      {error ? <Alert kind="err">{error}</Alert> : null}

      <form className="panel" onSubmit={save}>
        <h2>Add an asset</h2>
        <div className="grid">
          <Field label="Name">
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Type">
            <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="plant_equipment">Plant &amp; equipment</option>
              <option value="capital_works">Capital works (building)</option>
              <option value="other">Other</option>
            </select>
          </Field>
          <Field label="Purchase date">
            <input type="date" value={form.purchase_date} onChange={(e) => setForm({ ...form, purchase_date: e.target.value })} />
          </Field>
          <Field label="Cost">
            <input type="number" step="0.01" value={form.cost} onChange={(e) => setForm({ ...form, cost: e.target.value })} />
          </Field>
          <Field label="Effective life (years)">
            <input type="number" step="0.01" value={form.effective_life_years} onChange={(e) => setForm({ ...form, effective_life_years: e.target.value })} />
          </Field>
          <Field label="Method">
            <select value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}>
              <option value="diminishing_value">Diminishing value</option>
              <option value="prime_cost">Prime cost (straight line)</option>
            </select>
          </Field>
          <Field label="Business use (0–1)">
            <input type="number" step="0.0001" value={form.business_use_pct} onChange={(e) => setForm({ ...form, business_use_pct: e.target.value })} />
          </Field>
          <Field label="Low-value pool">
            <select value={String(form.low_value_pool)} onChange={(e) => setForm({ ...form, low_value_pool: e.target.value === 'true' })}>
              <option value="false">No</option>
              <option value="true">Yes</option>
            </select>
          </Field>
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <label>Receipt (PDF or image)</label>
            <input type="file" accept="application/pdf,image/*" ref={assetFileRef} />
          </div>
          <button
            type="button"
            className="ghost btn-busy"
            onClick={readReceipt}
            disabled={reading || !selected}
          >
            {reading ? (
              <>
                <span className="spinner" />
                Reading receipt…
              </>
            ) : (
              '✨ Read receipt with AI'
            )}
          </button>
        </div>

        {reading ? (
          <Alert kind="info">
            <span className="spinner" />
            Sending the receipt to DeepSeek — filling in the name, cost, date and an estimated
            effective life…
          </Alert>
        ) : null}
        {estimated && !reading ? (
          <div className="hint">
            Effective life ({form.effective_life_years || '—'} yrs) was{' '}
            <strong>estimated by AI</strong> — verify it against the ATO effective-life schedule.
          </div>
        ) : null}

        <div style={{ height: 12 }} />
        <button disabled={!selected}>Add asset</button>
      </form>

      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2 style={{ margin: 0 }}>Assets</h2>
          <button className="ghost small" onClick={recomputeAll} disabled={!selected}>
            Rebuild all schedules
          </button>
        </div>
        <table style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Asset</th>
              <th>Date</th>
              <th className="num">Cost</th>
              <th>Method</th>
              <th className="num">Business use</th>
              <th className="num">Latest deduction</th>
              <th>Receipt</th>
            </tr>
          </thead>
          <tbody>
            {items.map((a) => {
              const latest = a.depreciation_entries?.[a.depreciation_entries.length - 1]
              return (
                <tr key={a.id}>
                  <td>
                    {a.name}
                    {a.effective_life_is_estimate ? (
                      <>
                        {' '}
                        <span
                          className="badge warn"
                          title="Effective life was estimated (AI) — verify against the ATO schedule"
                        >
                          est. life
                        </span>
                      </>
                    ) : null}
                  </td>
                  <td>{a.purchase_date}</td>
                  <td className="num">{money(a.cost)}</td>
                  <td>{a.method === 'prime_cost' ? 'Prime cost' : 'Diminishing'}</td>
                  <td className="num">{pct(a.business_use_pct)}</td>
                  <td className="num">
                    {latest ? (
                      <>
                        {money(latest.deduction)} <span className="muted">({latest.financial_year})</span>
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td>
                    <AssetReceiptCell asset={a} onUploaded={load} />
                  </td>
                </tr>
              )
            })}
            {items.length === 0 ? (
              <tr>
                <td colSpan={7} className="muted">
                  No assets yet — add one above (attach its purchase receipt if you have it).
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </>
  )
}

function AssetReceiptCell({ asset, onUploaded }: { asset: Asset; onUploaded: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const upload = async (file: File) => {
    setBusy(true)
    setErr('')
    const body = new FormData()
    body.append('file', file)
    body.append('property', String(asset.property))
    body.append('asset', String(asset.id))
    body.append('original_name', file.name)
    try {
      await api('/api/receipts/', { method: 'POST', body })
      onUploaded()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="row">
      {asset.receipts.map((r) => (
        <a key={r.id} href={r.url} target="_blank" rel="noreferrer">
          📎
        </a>
      ))}
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,image/*"
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) upload(f)
          e.target.value = ''
        }}
      />
      <button type="button" className="ghost small" disabled={busy} onClick={() => inputRef.current?.click()}>
        {busy ? '…' : 'Upload'}
      </button>
      {err ? (
        <span className="badge err" title={err}>
          !
        </span>
      ) : null}
    </div>
  )
}
