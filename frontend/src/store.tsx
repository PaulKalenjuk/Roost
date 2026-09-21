import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { fetchList, type Property } from './api'

interface Ctx {
  properties: Property[]
  selectedId: number | null
  selected: Property | null
  select: (id: number | null) => void
  reload: () => Promise<void>
  loading: boolean
}

const PropertyCtx = createContext<Ctx | null>(null)

const STORAGE_KEY = 'roost.property'

export function PropertyProvider({ children }: { children: ReactNode }) {
  const [properties, setProperties] = useState<Property[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<number | null>(() => {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? Number(raw) : null
  })

  const reload = async () => {
    setLoading(true)
    try {
      setProperties(await fetchList<Property>('/api/properties/'))
    } finally {
      setLoading(false)
    }
  }

  const select = (id: number | null) => {
    setSelectedId(id)
    if (id === null) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, String(id))
  }

  useEffect(() => {
    reload()
  }, [])

  useEffect(() => {
    if (properties.length === 0) return
    const exists = properties.some((p) => p.id === selectedId)
    if (!exists) select(properties[0].id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [properties])

  const selected = properties.find((p) => p.id === selectedId) ?? null

  return (
    <PropertyCtx.Provider value={{ properties, selectedId, selected, select, reload, loading }}>
      {children}
    </PropertyCtx.Provider>
  )
}

export function useProperties(): Ctx {
  const ctx = useContext(PropertyCtx)
  if (!ctx) throw new Error('useProperties must be used inside PropertyProvider')
  return ctx
}
