import { useEffect, useState } from 'react'
import { api, fetchList, pct, type Listing, type Owner, type Ownership, type Property } from '../api'
import { Alert, Field } from '../components'
import { useProperties } from '../store'

const ATO_URL =
  'https://www.ato.gov.au/individuals-and-families/investments-and-assets/property-and-land/residential-rental-properties'

interface FormState {
  name: string
  address: string
  purchase_date: string
  purchase_price: string
  total_floor_area_sqm: string
  rental_floor_area_sqm: string
  let_percentage: string
  gst_registered: boolean
  default_depreciation_method: 'diminishing_value' | 'prime_cost'
  notes: string
}

const EMPTY: FormState = {
  name: '',
  address: '',
  purchase_date: '',
  purchase_price: '',
  total_floor_area_sqm: '',
  rental_floor_area_sqm: '',
  let_percentage: '',
  gst_registered: false,
  default_depreciation_method: 'diminishing_value',
  notes: '',
}

/** Empty optional inputs must go to the API as null, not "" (DRF rejects ""). */
const nullIfBlank = (value: string): string | null => (value.trim() === '' ? null : value)

interface OwnerRow {
  owner: number | ''
  /** Entered as a percentage (0–100); stored as a fraction. */
  percent: string
}

/**
 * Build the ownership rows: one row per existing owner, pre-filled with their
 * current share (percentage) where they already have one.  Any percentages the
 * user has already typed (`preserve`) win, so refreshing the owner list doesn't
 * wipe in-progress edits.
 */
function buildOwnerRows(allOwners: Owner[], ownerships: Ownership[], preserve?: OwnerRow[]): OwnerRow[] {
  const shareByOwner = new Map(ownerships.map((o) => [Number(o.owner), o.share_pct]))
  const typed = new Map(
    (preserve ?? []).filter((r) => r.owner).map((r) => [r.owner as number, r.percent]),
  )
  const rows: OwnerRow[] = allOwners.map((o) => {
    if (typed.has(o.id)) return { owner: o.id, percent: typed.get(o.id)! }
    const share = shareByOwner.get(o.id)
    return { owner: o.id, percent: share !== undefined ? String(Number(share) * 100) : '' }
  })
  if (rows.length === 0) rows.push({ owner: '', percent: '' })
  return rows
}

export default function PropertySetup() {
  const { properties, selectedId, select, reload } = useProperties()
  const selected = properties.find((p) => p.id === selectedId) ?? null

  const [form, setForm] = useState<FormState>(EMPTY)
  const [asset, setAsset] = useState<Partial<Property> | null>(null)
  const [owners, setOwners] = useState<Owner[]>([])
  const [listings, setListings] = useState<Listing[]>([])
  const [rows, setRows] = useState<OwnerRow[]>([])
  const [newOwner, setNewOwner] = useState({ name: '', email: '' })
  const [newListing, setNewListing] = useState({ name: '', platform: 'airbnb', external_id: '', url: '' })
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const refreshOwners = async () => setOwners(await fetchList<Owner>('/api/owners/'))

  useEffect(() => {
    refreshOwners().catch(() => {})
  }, [])

  useEffect(() => {
    if (!selected) {
      setForm(EMPTY)
      setAsset(null)
      setRows([])
      setListings([])
      return
    }
    setAsset(selected)
    setForm({
      name: selected.name,
      address: selected.address ?? '',
      purchase_date: selected.purchase_date ?? '',
      purchase_price: selected.purchase_price ?? '',
      total_floor_area_sqm: selected.total_floor_area_sqm ?? '',
      rental_floor_area_sqm: selected.rental_floor_area_sqm ?? '',
      let_percentage: selected.let_percentage ?? '',
      gst_registered: selected.gst_registered,
      default_depreciation_method: selected.default_depreciation_method,
      notes: selected.notes ?? '',
    })
    setRows(buildOwnerRows(owners, selected.ownerships ?? []))
    fetchList<Listing>(`/api/listings/?property=${selected.id}`).then(setListings).catch(() => {})
  }, [selectedId, properties])

  // When the owner list changes (e.g. a new owner was just created), add a row
  // for anyone missing — without discarding percentages already entered.
  const ownersKey = owners.map((o) => o.id).join(',')
  useEffect(() => {
    setRows((prev) => buildOwnerRows(owners, selected?.ownerships ?? [], prev))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownersKey])

  const saveProperty = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    setNotice('')
    const payload = {
      name: form.name,
      address: form.address,
      purchase_date: nullIfBlank(form.purchase_date),
      purchase_price: nullIfBlank(form.purchase_price),
      total_floor_area_sqm: nullIfBlank(form.total_floor_area_sqm),
      rental_floor_area_sqm: nullIfBlank(form.rental_floor_area_sqm),
      let_percentage: nullIfBlank(form.let_percentage),
      gst_registered: form.gst_registered,
      default_depreciation_method: form.default_depreciation_method,
      notes: form.notes,
    }
    try {
      if (asset?.id) {
        await api(`/api/properties/${asset.id}/`, { method: 'PATCH', body: JSON.stringify(payload) })
        setNotice('Property saved.')
        await reload()
      } else {
        const created = await api<Property>('/api/properties/', {
          method: 'POST',
          body: JSON.stringify(payload),
        })
        await reload()
        select(created.id) // jump to the property we just made
        setNotice('Property created — now add the owners below.')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  const saveOwnerships = async () => {
    if (!asset?.id) return
    setError('')
    setNotice('')
    const filled = rows.filter((r) => r.owner)
    const total = filled.reduce((sum, r) => sum + Number(r.percent || 0), 0)
    if (filled.length === 0) {
      setError('Add at least one owner row first.')
      return
    }
    const seen = new Set<number | ''>()
    for (const r of filled) {
      if (seen.has(r.owner)) {
        setError('Each owner can only appear once.')
        return
      }
      seen.add(r.owner)
    }
    if (Math.abs(total - 100) > 0.01) {
      setError(`Ownership percentages must total 100% (currently ${total.toFixed(2)}%).`)
      return
    }
    setBusy(true)
    try {
      for (const existing of asset.ownerships ?? []) {
        await api(`/api/ownerships/${existing.id}/`, { method: 'DELETE' })
      }
      for (const r of filled) {
        await api('/api/ownerships/', {
          method: 'POST',
          body: JSON.stringify({
            property: asset.id,
            owner: r.owner,
            share_pct: (Number(r.percent || 0) / 100).toFixed(4),
          }),
        })
      }
      await reload()
      setNotice('Ownership saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  const addOwner = async () => {
    if (!newOwner.name.trim()) return
    setError('')
    try {
      await api('/api/owners/', { method: 'POST', body: JSON.stringify(newOwner) })
      setNewOwner({ name: '', email: '' })
      await refreshOwners()
      setNotice('Owner added — add them to the ownership rows below.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add owner')
    }
  }

  const addListing = async () => {
    if (!asset?.id || !newListing.name.trim()) return
    setError('')
    try {
      await api('/api/listings/', {
        method: 'POST',
        body: JSON.stringify({ ...newListing, property: asset.id }),
      })
      setNewListing({ name: '', platform: 'airbnb', external_id: '', url: '' })
      setListings(await fetchList(`/api/listings/?property=${asset.id}`))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add listing')
    }
  }

  const ownsTotal = rows.filter((r) => r.owner).reduce((sum, r) => sum + Number(r.percent || 0), 0)
  const ownsOk = Math.abs(ownsTotal - 100) < 0.01

  return (
    <>
      <h1>Property setup</h1>
      <p className="sub">
        Set the dwelling, how much of it is let, GST status, ownership splits and the
        listing(s).
      </p>

      <div className="panel">
        <div className="row">
          <div style={{ flex: 1 }}>
            <Field label="Working on">
              <select
                value={selectedId ?? ''}
                onChange={(e) => select(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">— new property —</option>
                {properties.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <button className="ghost" onClick={() => select(null)}>
            + New property
          </button>
        </div>
      </div>

      {notice ? <Alert kind="ok">{notice}</Alert> : null}
      {error ? <Alert kind="err">{error}</Alert> : null}

      <form className="panel" onSubmit={saveProperty}>
        <h2>{asset?.id ? 'Edit property' : 'New property'}</h2>
        <div className="grid">
          <Field label="Name">
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          </Field>
          <Field label="Address">
            <input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
          </Field>
          <Field label="Purchase date">
            <input type="date" value={form.purchase_date} onChange={(e) => setForm({ ...form, purchase_date: e.target.value })} />
          </Field>
          <Field label="Purchase price">
            <input type="number" step="0.01" value={form.purchase_price} onChange={(e) => setForm({ ...form, purchase_price: e.target.value })} />
          </Field>
        </div>

        <h3>Let share</h3>
        <div className="grid">
          <Field label="Total floor area (m²)">
            <input type="number" step="0.01" value={form.total_floor_area_sqm} onChange={(e) => setForm({ ...form, total_floor_area_sqm: e.target.value })} />
          </Field>
          <Field label="Let floor area (m²)">
            <input type="number" step="0.01" value={form.rental_floor_area_sqm} onChange={(e) => setForm({ ...form, rental_floor_area_sqm: e.target.value })} />
          </Field>
          <Field
            label="Let percentage override (0–1)"
            hint={`Leave blank to use the floor-area ratio${asset ? ` (currently ${pct(asset.let_share)})` : ''}.`}
          >
            <input type="number" step="0.0001" value={form.let_percentage} onChange={(e) => setForm({ ...form, let_percentage: e.target.value })} />
          </Field>
        </div>

        <h3>Tax treatment</h3>
        <div className="grid">
          <Field label="GST registered">
            <select value={String(form.gst_registered)} onChange={(e) => setForm({ ...form, gst_registered: e.target.value === 'true' })}>
              <option value="false">No</option>
              <option value="true">Yes</option>
            </select>
          </Field>
          <Field
            label="Default depreciation method"
            hint={
              <>
                See the ATO for the implications:{' '}
                <a href={ATO_URL} target="_blank" rel="noreferrer">
                  residential rental properties
                </a>
                .
              </>
            }
          >
            <select
              value={form.default_depreciation_method}
              onChange={(e) =>
                setForm({ ...form, default_depreciation_method: e.target.value as FormState['default_depreciation_method'] })
              }
            >
              <option value="diminishing_value">Diminishing value</option>
              <option value="prime_cost">Prime cost (straight line)</option>
            </select>
          </Field>
        </div>
        <div style={{ height: 12 }} />
        <Field label="Notes">
          <textarea rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
        </Field>
        <div style={{ height: 14 }} />
        <button disabled={busy}>{asset?.id ? 'Save property' : 'Create property'}</button>
      </form>

      {asset?.id ? (
        <>
          <div className="panel">
            <h2>Ownership</h2>
            <p className="sub">
              Add each owner and their <strong>percentage</strong>. Percentages must total 100%.
              Reporting splits every line by these shares.
            </p>

            {rows.map((r, index) => (
              <div className="owner-row" key={index}>
                <div>
                  <label>Owner</label>
                  <select
                    value={r.owner}
                    onChange={(e) => {
                      const copy = [...rows]
                      copy[index] = { ...copy[index], owner: e.target.value ? Number(e.target.value) : '' }
                      setRows(copy)
                    }}
                  >
                    <option value="">— select owner —</option>
                    {owners.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label>Ownership %</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    max="100"
                    placeholder="e.g. 60"
                    value={r.percent}
                    onChange={(e) => {
                      const copy = [...rows]
                      copy[index] = { ...copy[index], percent: e.target.value }
                      setRows(copy)
                    }}
                  />
                </div>
                <button type="button" className="ghost small" onClick={() => setRows(rows.filter((_, i) => i !== index))}>
                  Remove
                </button>
              </div>
            ))}

            {rows.length === 0 ? (
              <p className="muted">No owners yet — click “+ Add owner”.</p>
            ) : null}

            <div className="row">
              <button type="button" className="ghost small" onClick={() => setRows([...rows, { owner: '', percent: '' }])}>
                + Add owner
              </button>
              <span className={`badge ${ownsOk ? 'ok' : 'warn'}`}>Total {ownsTotal.toFixed(2)}%</span>
              <button type="button" className="small" onClick={saveOwnerships} disabled={busy || !ownsOk}>
                Save ownership
              </button>
            </div>

            <h3>New owner</h3>
            <p className="hint" style={{ marginTop: 0 }}>
              Create the person first, then assign them above.
            </p>
            <div className="row">
              <div>
                <label>Name</label>
                <input value={newOwner.name} onChange={(e) => setNewOwner({ ...newOwner, name: e.target.value })} />
              </div>
              <div>
                <label>Email</label>
                <input value={newOwner.email} onChange={(e) => setNewOwner({ ...newOwner, email: e.target.value })} />
              </div>
              <button type="button" className="ghost small" onClick={addOwner}>
                + Create owner
              </button>
            </div>
          </div>

          <div className="panel">
            <h2>Listings</h2>
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Platform</th>
                  <th>Listing ID</th>
                </tr>
              </thead>
              <tbody>
                {listings.map((l) => (
                  <tr key={l.id}>
                    <td>{l.name}</td>
                    <td>{l.platform}</td>
                    <td>{l.external_id || '—'}</td>
                  </tr>
                ))}
                {listings.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="muted">
                      No listings yet.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
            <div className="row" style={{ marginTop: 12 }}>
              <div>
                <label>Listing name</label>
                <input value={newListing.name} onChange={(e) => setNewListing({ ...newListing, name: e.target.value })} />
              </div>
              <div>
                <label>Platform</label>
                <select value={newListing.platform} onChange={(e) => setNewListing({ ...newListing, platform: e.target.value })}>
                  <option value="airbnb">Airbnb</option>
                  <option value="other">Other</option>
                </select>
              </div>
              <div>
                <label>Listing ID</label>
                <input value={newListing.external_id} onChange={(e) => setNewListing({ ...newListing, external_id: e.target.value })} />
              </div>
              <button type="button" className="ghost small" onClick={addListing}>
                + Add listing
              </button>
            </div>
          </div>

          <p className="muted">
            Let share: <strong>{pct(asset.let_share)}</strong> · Ownership total:{' '}
            <strong>{ownsTotal.toFixed(2)}%</strong>
          </p>
        </>
      ) : (
        <Alert kind="info">
          Create the property first — then the ownership, listing and tax options appear here.
        </Alert>
      )}
    </>
  )
}
