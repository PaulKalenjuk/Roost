import { useEffect, useMemo, useState } from 'react'
import { api, fetchList, pct, type Owner, type Property } from '../api'
import { Alert, Field } from '../components'

interface Ownership {
  id: number
  property: number
  owner: number
  owner_name: string
  share_pct: string
}

const BAS_OPTIONS = [
  { value: 'monthly', label: 'Monthly' },
  { value: 'quarterly', label: 'Quarterly' },
  { value: 'yearly', label: 'Yearly' },
]

export default function Owners() {
  const [owners, setOwners] = useState<Owner[]>([])
  const [ownerships, setOwnerships] = useState<Ownership[]>([])
  const [properties, setProperties] = useState<Property[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState({
    name: '',
    email: '',
    bas_frequency: 'yearly',
    notes: '',
  })

  const load = async () => {
    const [o, w, p] = await Promise.all([
      fetchList<Owner>('/api/owners/'),
      fetchList<Ownership>('/api/ownerships/'),
      fetchList<Property>('/api/properties/'),
    ])
    setOwners(o)
    setOwnerships(w)
    setProperties(p)
  }
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const propertiesFor = useMemo(() => {
    const map = new Map<number, string[]>()
    const nameOf = new Map(properties.map((p) => [p.id, p.name]))
    for (const o of ownerships) {
      const list = map.get(o.owner) ?? []
      list.push(`${nameOf.get(o.property) ?? `#${o.property}`} (${pct(o.share_pct)})`)
      map.set(o.owner, list)
    }
    return map
  }, [ownerships, properties])

  const resetForm = () => {
    setEditingId(null)
    setForm({ name: '', email: '', bas_frequency: 'yearly', notes: '' })
  }

  const startEdit = (o: Owner) => {
    setEditingId(o.id)
    setForm({
      name: o.name,
      email: o.email ?? '',
      bas_frequency: o.bas_frequency,
      notes: o.notes ?? '',
    })
    setError('')
    setNotice(`Editing ${o.name}.`)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name.trim()) {
      setError('Give the owner a name.')
      return
    }
    setError('')
    setNotice('')
    const payload = { ...form, name: form.name.trim() }
    try {
      if (editingId) {
        await api(`/api/owners/${editingId}/`, { method: 'PATCH', body: JSON.stringify(payload) })
        setNotice('Owner updated.')
      } else {
        await api('/api/owners/', { method: 'POST', body: JSON.stringify(payload) })
        setNotice('Owner added.')
      }
      resetForm()
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }

  const remove = async (o: Owner) => {
    if (
      !window.confirm(
        `Delete “${o.name}”? Their ownership shares and any income assigned to them will be cleared.`,
      )
    )
      return
    try {
      await api(`/api/owners/${o.id}/`, { method: 'DELETE' })
      if (editingId === o.id) resetForm()
      await load()
      setNotice('Owner deleted.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  return (
    <>
      <h1>Owners</h1>
      <p className="sub">
        Each owner can have a <strong>BAS frequency</strong> — how often they lodge a
        BAS. The Reporting page defaults its period to the selected owner's frequency
        (monthly, quarterly or yearly). Ownership shares per property are set on the
        Property setup page.
      </p>

      {notice ? <Alert kind="ok">{notice}</Alert> : null}
      {error ? <Alert kind="err">{error}</Alert> : null}

      <div className="panel">
        <h2>Owners</h2>
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>BAS frequency</th>
                <th>Properties</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {owners.map((o) => (
                <tr key={o.id} className={o.id === editingId ? 'selected-row' : ''}>
                  <td>{o.name}</td>
                  <td>{o.email || '—'}</td>
                  <td>{BAS_OPTIONS.find((b) => b.value === o.bas_frequency)?.label ?? o.bas_frequency}</td>
                  <td>{(propertiesFor.get(o.id) ?? []).join(', ') || '—'}</td>
                  <td>
                    <div className="row">
                      <button type="button" className="ghost small" onClick={() => startEdit(o)}>
                        Edit
                      </button>
                      <button type="button" className="ghost small" onClick={() => remove(o)}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {owners.length === 0 ? (
                <tr>
                  <td colSpan={5} className="muted">
                    No owners yet — add one below.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <form onSubmit={save} style={{ marginTop: 14 }}>
          <h3>{editingId ? 'Edit owner' : 'Add an owner'}</h3>
          <div className="grid">
            <Field label="Name">
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Email">
              <input
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </Field>
            <Field label="BAS frequency" hint="Defaults the reporting period for this owner.">
              <select
                value={form.bas_frequency}
                onChange={(e) => setForm({ ...form, bas_frequency: e.target.value })}
              >
                {BAS_OPTIONS.map((b) => (
                  <option key={b.value} value={b.value}>
                    {b.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Notes">
              <input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </Field>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button>{editingId ? 'Save changes' : 'Add owner'}</button>
            {editingId ? (
              <button type="button" className="ghost" onClick={resetForm}>
                Cancel
              </button>
            ) : null}
          </div>
        </form>
      </div>
    </>
  )
}
