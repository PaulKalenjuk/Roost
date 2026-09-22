import { useEffect, useState } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { api, ensureCsrf } from './api'
import { Alert, Field } from './components'
import { PropertyProvider } from './store'
import PropertySetup from './pages/PropertySetup'
import Income from './pages/Income'
import Expenses from './pages/Expenses'
import Assets from './pages/Assets'
import Reporting from './pages/Reporting'

interface Me {
  authenticated: boolean
  username: string
  is_staff: boolean
}

const NAV = [
  { to: '/', label: 'Property setup', end: true },
  { to: '/income', label: 'Income' },
  { to: '/expenses', label: 'Expenses' },
  { to: '/assets', label: 'Depreciating assets' },
  { to: '/reporting', label: 'Reporting' },
]

export default function App() {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)
  // On narrow screens the nav is an off-canvas drawer; on desktop it's always
  // visible (the drawer classes simply have no effect above the breakpoint).
  const [navOpen, setNavOpen] = useState(false)
  const location = useLocation()

  // Close the drawer on every navigation, so a tap doesn't leave it covering
  // the page you just opened.
  useEffect(() => {
    setNavOpen(false)
  }, [location.pathname])

  // Escape closes it, and the page behind must not scroll while it's open.
  useEffect(() => {
    if (!navOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setNavOpen(false)
    }
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [navOpen])

  const refresh = async () => {
    try {
      setMe(await api<Me>('/api/auth/me/'))
    } catch {
      setMe({ authenticated: false, username: '', is_staff: false })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    ensureCsrf().finally(refresh)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (loading) return <div className="main">Loading…</div>
  if (!me?.authenticated) return <Login onDone={refresh} />

  return (
    <PropertyProvider>
      <div className="shell">
        <header className="topbar">
          <button
            type="button"
            className="menu-toggle"
            onClick={() => setNavOpen((open) => !open)}
            aria-label={navOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={navOpen}
            aria-controls="roost-nav"
          >
            <span aria-hidden="true">{navOpen ? '✕' : '☰'}</span>
          </button>
          <div className="brand">
            Roost<span>.</span>
          </div>
        </header>
        <aside id="roost-nav" className={`sidebar${navOpen ? ' open' : ''}`}>
          <div className="sidebar-head">
            <div className="brand">
              Roost<span>.</span>
            </div>
            <button
              type="button"
              className="nav-close"
              aria-label="Close menu"
              onClick={() => setNavOpen(false)}
            >
              <span aria-hidden="true">✕</span>
            </button>
          </div>
          {NAV.map((item) => (
            <NavLink key={item.to} className="navlink" to={item.to} end={item.end}>
              {item.label}
            </NavLink>
          ))}
          <div className="spacer" />
          <div className="who">Signed in as {me.username}</div>
          <a className="navlink" href="/admin/">
            Django admin ↗
          </a>
          <button
            className="ghost small"
            onClick={async () => {
              await api('/api/auth/logout/', { method: 'POST' })
              refresh()
            }}
          >
            Sign out
          </button>
        </aside>
        {navOpen ? (
          <button
            type="button"
            className="backdrop"
            aria-label="Close menu"
            onClick={() => setNavOpen(false)}
          />
        ) : null}
        <main className="main">
          <Routes>
            <Route path="/" element={<PropertySetup />} />
            <Route path="/income" element={<Income />} />
            <Route path="/expenses" element={<Expenses />} />
            <Route path="/assets" element={<Assets />} />
            <Route path="/reporting" element={<Reporting />} />
          </Routes>
        </main>
      </div>
    </PropertyProvider>
  )
}

function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api('/api/auth/login/', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="login panel" onSubmit={submit}>
      <h1>Roost</h1>
      <p className="sub">Sign in to manage your short-stay ledger.</p>
      {error ? <Alert kind="err">{error}</Alert> : null}
      <Field label="Username">
        <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
      </Field>
      <div style={{ height: 12 }} />
      <Field label="Password">
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <div style={{ height: 16 }} />
      <button disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
    </form>
  )
}
