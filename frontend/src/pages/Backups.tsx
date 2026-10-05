import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Alert, Field } from '../components'

interface RestorePoint {
  stamp: string
  db_bytes: number
  media_bytes: number
  created: string | null
  complete: boolean
}

interface Account {
  email: string | null
  displayName: string | null
  changed_at?: string
}

interface Quota {
  total: number
  used: number
  free: number
}

interface BackupStatus {
  running: boolean
  remote: string
  drive_remote: string
  keep: number
  schedule: string
  account: Account | null
  quota: Quota | null
  local: RestorePoint[]
  remote_points: RestorePoint[] | null
}

function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(i === 0 || v >= 10 ? 0 : 1)} ${units[i]}`
}

function when(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

export default function Backups() {
  const [status, setStatus] = useState<BackupStatus | null>(null)
  const [log, setLog] = useState<string[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [acctBusy, setAcctBusy] = useState(false)
  const [showChange, setShowChange] = useState(false)
  const [token, setToken] = useState('')
  const pollRef = useRef<number | null>(null)

  const load = useCallback(async () => {
    try {
      const s = await api<BackupStatus>('/api/backups/status/')
      setStatus(s)
      const l = await api<{ lines: string[] }>('/api/backups/log/?lines=200')
      setLog(l.lines)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the backup status')
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // While a backup is running, refresh every few seconds so the log/status keep up.
  useEffect(() => {
    if (status?.running && pollRef.current === null) {
      pollRef.current = window.setInterval(load, 4000)
    } else if (!status?.running && pollRef.current !== null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [status?.running, load])

  useEffect(
    () => () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current)
    },
    [],
  )

  const runNow = async () => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const r = await api<{ ok: boolean; message: string }>('/api/backups/run/', {
        method: 'POST',
      })
      setNotice(r.message)
      window.setTimeout(load, 3000)
      window.setTimeout(load, 9000)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start a backup')
    } finally {
      setBusy(false)
    }
  }

  const changeAccount = async () => {
    setAcctBusy(true)
    setError('')
    setNotice('')
    try {
      const r = await api<{ ok: boolean; message: string }>('/api/backups/account/', {
        method: 'POST',
        body: JSON.stringify({ token }),
      })
      setNotice(`Account updated: ${r.message}`)
      setToken('')
      setShowChange(false)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not change the account')
    } finally {
      setAcctBusy(false)
    }
  }

  return (
    <>
      <h1>Backups</h1>
      <p className="sub">
        Nightly, change-aware backups of the database and media, kept locally and mirrored
        (encrypted) to Google Drive. The backup runs on the host; here you can watch the log,
        see the restore points, run one now, and manage the Drive account.
      </p>

      {error ? <Alert kind="err">{error}</Alert> : null}
      {notice ? <Alert kind="ok">{notice}</Alert> : null}

      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h2 style={{ margin: 0 }}>Status</h2>
          <span className={`badge ${status?.running ? 'warn' : 'ok'}`}>
            {status?.running ? 'backup running…' : 'idle'}
          </span>
        </div>
        <div className="grid" style={{ marginTop: 10 }}>
          <div><label>Schedule</label><div>{status?.schedule ?? '—'}</div></div>
          <div><label>Keep</label><div>{status ? `last ${status.keep}` : '—'}</div></div>
          <div><label>Remote (encrypted)</label><div>{status?.remote ?? '—'}</div></div>
          <div>
            <label>Drive account</label>
            <div>{status?.account?.email ?? status?.account?.displayName ?? '—'}</div>
          </div>
          <div>
            <label>Drive usage</label>
            <div>
              {status?.quota
                ? `${bytes(status.quota.used)} of ${bytes(status.quota.total)}`
                : '—'}
            </div>
          </div>
        </div>
        <div style={{ height: 12 }} />
        <div className="row">
          <button type="button" onClick={runNow} disabled={busy || status?.running}>
            {status?.running ? 'Running…' : busy ? 'Requesting…' : 'Back up now'}
          </button>
          <button type="button" className="ghost" onClick={load}>
            Refresh
          </button>
        </div>
        <p className="hint" style={{ marginTop: 8 }}>
          “Back up now” forces a run even if nothing has changed; the scheduled run only
          creates a new restore point when the data has actually changed.
        </p>
      </div>

      <div className="panel">
        <h2>Restore points (local)</h2>
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Taken</th>
                <th className="num">Database</th>
                <th className="num">Media</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {(status?.local ?? []).map((p) => (
                <tr key={p.stamp}>
                  <td>{when(p.created)}</td>
                  <td className="num">{bytes(p.db_bytes)}</td>
                  <td className="num">{bytes(p.media_bytes)}</td>
                  <td>{p.complete ? 'complete' : 'incomplete'}</td>
                </tr>
              ))}
              {status && status.local.length === 0 ? (
                <tr>
                  <td colSpan={4} className="muted">No restore points yet.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <h2>Restore points (Google Drive, encrypted)</h2>
        {status?.remote_points === null ? (
          <Alert kind="err">The Drive remote isn’t reachable right now.</Alert>
        ) : (
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Taken</th>
                  <th className="num">Database</th>
                  <th className="num">Media</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {(status?.remote_points ?? []).map((p) => (
                  <tr key={p.stamp}>
                    <td>{when(p.created)}</td>
                    <td className="num">{bytes(p.db_bytes)}</td>
                    <td className="num">{bytes(p.media_bytes)}</td>
                    <td>{p.complete ? 'complete' : 'incomplete'}</td>
                  </tr>
                ))}
                {status && (status.remote_points?.length ?? 0) === 0 ? (
                  <tr>
                    <td colSpan={4} className="muted">Nothing on Drive yet.</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h2 style={{ margin: 0 }}>Backup log</h2>
          <span className="muted">most recent 200 lines</span>
        </div>
        <pre className="logbox">{log.length ? log.join('\n') : 'No log yet.'}</pre>
      </div>

      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h2 style={{ margin: 0 }}>Google Drive account</h2>
          {!showChange ? (
            <button type="button" className="ghost small" onClick={() => setShowChange(true)}>
              Change account
            </button>
          ) : null}
        </div>
        <p className="sub" style={{ marginTop: 6 }}>
          Currently backing up to{' '}
          <strong>{status?.account?.email ?? status?.account?.displayName ?? 'the connected account'}</strong>{' '}
          ({status?.drive_remote ?? '—'}).
        </p>
        {showChange ? (
          <>
            <div className="hint">
              On a computer with a browser, run <code>rclone authorize "drive"</code>, sign in
              with the Google account you want to use, click <em>Allow</em>, then paste the
              JSON it prints below. The connection is tested before it is saved.
            </div>
            <Field label="rclone token (JSON)">
              <textarea
                rows={4}
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder={'{"access_token":"…","refresh_token":"…","expiry":"…"}'}
                style={{ width: '100%', fontFamily: 'monospace', fontSize: 13 }}
              />
            </Field>
            <div className="row" style={{ marginTop: 8 }}>
              <button type="button" onClick={changeAccount} disabled={acctBusy || !token.trim()}>
                {acctBusy ? 'Testing…' : 'Save account'}
              </button>
              <button
                type="button"
                className="ghost"
                onClick={() => {
                  setShowChange(false)
                  setToken('')
                }}
              >
                Cancel
              </button>
            </div>
            <p className="hint">
              The encryption password is unchanged, so existing Drive backups stay readable.
              Keep that password safe — without it the backups can’t be decrypted.
            </p>
          </>
        ) : null}
      </div>
    </>
  )
}
