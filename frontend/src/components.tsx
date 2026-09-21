import type { ReactNode } from 'react'
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
