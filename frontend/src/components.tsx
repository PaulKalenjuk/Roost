import { useEffect, useState, type ReactNode } from 'react'
import { api, type Category, type Coverage } from './api'
import { useProperties } from './store'

export function Alert({ kind, children }: { kind: 'ok' | 'err' | 'info'; children: ReactNode }) {
  if (!children) return null
  return <div className={`msg ${kind}`}>{children}</div>
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: ReactNode
  children: ReactNode
}) {
  return (
    <div>
      <label>{label}</label>
      {children}
      {hint ? <div className="hint">{hint}</div> : null}
    </div>
  )
}

/** Property picker shared across the pages. */
export function PropertyPicker() {
  const { properties, selectedId, select } = useProperties()
  if (properties.length === 0) return null
  return (
    <div>
      <label>Property</label>
      <select
        value={selectedId ?? ''}
        onChange={(e) => select(e.target.value ? Number(e.target.value) : null)}
      >
        {properties.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </div>
  )
}

export function Loading({ what = 'data' }: { what?: string }) {
  return <p className="muted">Loading {what}…</p>
}

/** Proportional strip showing covered (green) vs uncovered (red) periods. */
export function CoverageBar({ coverage }: { coverage: Coverage }) {
  if (!coverage.computable) return null
  return (
    <div style={{ marginTop: 12 }}>
      <div className="covbar">
        {coverage.segments.map((s, i) => (
          <div
            key={i}
            className={`covseg ${s.kind === 'covered' ? 'covered' : 'gap'}`}
            style={{ width: `${s.width_pct}%` }}
            title={`${s.kind === 'covered' ? 'Covered' : 'Uncovered'}: ${s.start} → ${s.end} (${s.days} days)`}
          />
        ))}
      </div>
      <div className="row" style={{ justifyContent: 'space-between', fontSize: 12 }}>
        <span className="muted">{coverage.start}</span>
        <span className="muted">{coverage.end}</span>
      </div>
    </div>
  )
}

/** Compact badge for the utility-types table. */
export function CoverageCell({ cov }: { cov?: Coverage }) {
  if (!cov) return <span className="muted">—</span>
  if (!cov.computable) {
    return (
      <span className="badge warn" title={cov.reason ?? ''}>
        set dates
      </span>
    )
  }
  const ok = cov.gaps.length === 0
  return (
    <span
      className={`badge ${ok ? 'ok' : 'warn'}`}
      title={`${cov.covered_days} of ${cov.total_days} days covered`}
    >
      {cov.coverage_pct.toFixed(0)}%
      {ok ? '' : ` · ${cov.gaps.length} gap${cov.gaps.length > 1 ? 's' : ''}`}
    </span>
  )
}

const CATEGORY_KINDS = [
  { value: 'utility', label: 'Utility' },
  { value: 'operating', label: 'Operating expense' },
  { value: 'capital_works', label: 'Capital works' },
  { value: 'mortgage', label: 'Interest / mortgage' },
  { value: 'other', label: 'Other' },
]

const APPORTION_FOR_KIND: Record<string, string> = {
  utility: 'area',
  operating: 'none',
  capital_works: 'none',
  mortgage: 'none',
  other: 'none',
}

/**
 * A category dropdown with a built-in "+ Add new category…" option, so you can
 * create one without leaving the form.  Auto-opens the creator when there are no
 * categories at all.
 */
export function CategoryPicker({
  value,
  onChange,
  categories,
  onChanged,
  defaultKind = 'operating',
  preferKinds,
}: {
  value: string | number
  onChange: (id: number | '') => void
  categories: Category[]
  onChanged: () => Promise<void> | void
  defaultKind?: string
  preferKinds?: string[]
}) {
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [kind, setKind] = useState(defaultKind)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // No categories yet? open the creator straight away.
  useEffect(() => {
    if (categories.length === 0) setAdding(true)
  }, [categories.length])

  const kinds = preferKinds
    ? CATEGORY_KINDS.slice().sort((a, b) => Number(preferKinds.includes(b.value)) - Number(preferKinds.includes(a.value)))
    : CATEGORY_KINDS

  const create = async () => {
    if (!name.trim()) {
      setErr('Give the category a name.')
      return
    }
    setBusy(true)
    setErr('')
    try {
      const created = await api<Category>('/api/categories/', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          kind,
          default_apportionment: APPORTION_FOR_KIND[kind] ?? 'none',
        }),
      })
      await onChanged()
      onChange(created.id)
      setName('')
      setAdding(false)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not add category')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <select
        value={value}
        onChange={(e) => {
          if (e.target.value === '__new__') {
            setAdding(true)
            return
          }
          onChange(e.target.value ? Number(e.target.value) : '')
        }}
      >
        <option value="">— select —</option>
        {categories.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
        <option value="__new__">+ Add new category…</option>
      </select>

      {adding ? (
        <div className="row" style={{ marginTop: 8 }}>
          <div style={{ flex: 1 }}>
            <input
              placeholder="New category name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  create()
                }
              }}
            />
          </div>
          <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: 170 }}>
            {kinds.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
          <button type="button" className="small" onClick={create} disabled={busy}>
            {busy ? '…' : 'Add'}
          </button>
          {categories.length > 0 ? (
            <button type="button" className="ghost small" onClick={() => setAdding(false)}>
              Cancel
            </button>
          ) : null}
        </div>
      ) : null}
      {err ? (
        <div className="hint" style={{ color: 'var(--danger)' }}>
          {err}
        </div>
      ) : null}
      {categories.length === 0 && !err ? (
        <div className="hint">No categories yet — add your first one here.</div>
      ) : null}
    </>
  )
}
