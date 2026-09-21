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
  const assetImageRef = useRef<HTMLInputElement>(null)
  const [editingAssetId, setEditingAssetId] = useState<number | null>(null)
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
    if (selected && !editingAssetId) {
      setForm((f) => ({
        ...f,
        method: selected.default_depreciation_method,
        business_use_pct: selected.let_share ?? '',
      }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, selected?.default_depreciation_method, selected?.let_share])

  const resetForm = () => {
    setEditingAssetId(null)
    setEstimated(false)
    setForm({
      name: '',
      kind: 'plant_equipment',
      purchase_date: new Date().toISOString().slice(0, 10),
      cost: '',
      effective_life_years: '',
      method: selected?.default_depreciation_method ?? 'diminishing_value',
      business_use_pct: selected?.let_share ?? '',
      low_value_pool: false,
    })
    if (assetFileRef.current) assetFileRef.current.value = ''
    if (assetImageRef.current) assetImageRef.current.value = ''
  }

  const startEdit = (a: Asset) => {
    setEditingAssetId(a.id)
    setEstimated(Boolean(a.effective_life_is_estimate))
    setForm({
      name: a.name,
      kind: a.kind,
      purchase_date: a.purchase_date ?? '',
      cost: a.cost ?? '',
      effective_life_years: a.effective_life_years ?? '',
      method: a.method,
      business_use_pct: a.business_use_pct ?? '',
      low_value_pool: a.low_value_pool,
    })
    if (assetFileRef.current) assetFileRef.current.value = ''
    if (assetImageRef.current) assetImageRef.current.value = ''
    setError('')
    setNotice(`Editing “${a.name}”.`)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const deleteAsset = async (a: Asset) => {
    if (!window.confirm(`Delete the asset “${a.name}” and its receipts?`)) return
    try {
      await api(`/api/assets/${a.id}/`, { method: 'DELETE' })
      if (editingAssetId === a.id) resetForm()
      await load()
      setNotice('Asset deleted.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selected) return
    setError('')
    setNotice('')
    if (!form.name.trim() || !form.cost) {
      setError('Name and cost are required.')
      return
    }
    const body = new FormData()
    body.append('property', String(selected.id))
    body.append('name', form.name)
    body.append('kind', form.kind)
    body.append('purchase_date', form.purchase_date)
    body.append('cost', form.cost)
    body.append('effective_life_years', form.effective_life_years)
    body.append('method', form.method)
    body.append('business_use_pct', form.business_use_pct || '1')
    body.append('low_value_pool', String(form.low_value_pool))
    body.append('effective_life_is_estimate', String(estimated))
    const image = assetImageRef.current?.files?.[0]
    if (image) body.append('image', image)

    try {
      let assetId: number
      if (editingAssetId) {
        const updated = await api<Asset>(`/api/assets/${editingAssetId}/`, {
          method: 'PATCH',
          body,
        })
        assetId = updated.id
        setNotice('Asset updated and its depreciation recalculated.')
      } else {
        const created = await api<Asset>('/api/assets/', { method: 'POST', body })
        assetId = created.id
        setNotice('Asset added and depreciation schedule built.')
      }
      await api(`/api/assets/${assetId}/recompute/`, { method: 'POST' })

      const receipt = assetFileRef.current?.files?.[0]
      if (receipt) {
        const receiptBody = new FormData()
        receiptBody.append('file', receipt)
        receiptBody.append('property', String(selected.id))
        receiptBody.append('asset', String(assetId))
        receiptBody.append('original_name', receipt.name)
        await api('/api/receipts/', { method: 'POST', body: receiptBody })
      }
      resetForm()
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
        <h2>{editingAssetId ? 'Edit asset' : 'Add an asset'}</h2>
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
          <div>
            <label>Asset photo (image)</label>
            <input type="file" accept="image/*" ref={assetImageRef} />
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
        <div className="row">
          <button disabled={!selected}>{editingAssetId ? 'Save changes' : 'Add asset'}</button>
          {editingAssetId ? (
            <button type="button" className="ghost" onClick={resetForm}>
              Cancel
            </button>
          ) : null}
        </div>
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
              <th>Photo</th>
              <th>Date</th>
              <th className="num">Cost</th>
              <th>Method</th>
              <th className="num">Business use</th>
              <th className="num">Latest deduction</th>
              <th>Receipt</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((a) => {
              const latest = a.depreciation_entries?.[a.depreciation_entries.length - 1]
              return (
                <tr key={a.id} className={a.id === editingAssetId ? 'selected-row' : ''}>
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
                  <td>
                    {a.image_url ? (
                      <a href={a.image_url} target="_blank" rel="noreferrer">
                        <img
                          src={a.image_url}
                          alt={a.name}
                          style={{
                            width: 44,
                            height: 44,
                            objectFit: 'cover',
                            borderRadius: 6,
                            border: '1px solid var(--line)',
                            display: 'block',
                          }}
                        />
                      </a>
                    ) : (
                      '—'
                    )}
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
                  <td>
                    <div className="row">
                      <button type="button" className="ghost small" onClick={() => startEdit(a)}>
                        Edit
                      </button>
                      <button type="button" className="ghost small" onClick={() => deleteAsset(a)}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
            {items.length === 0 ? (
              <tr>
                <td colSpan={9} className="muted">
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
