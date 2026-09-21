import { useEffect, useRef, useState } from 'react'
import {
  api,
  fetchList,
  money,
  type BillExtraction,
  type Category,
  type Expense,
  type Receipt,
  type UtilityBill,
  type UtilityType,
} from '../api'
import { Alert, CategoryPicker, Field } from '../components'
import { useProperties } from '../store'

const APPORTION_LABELS: Record<string, string> = {
  none: 'Fully deductible',
  area: 'By floor area of the let',
  custom: 'Custom %',
}

export default function Expenses() {
  const [tab, setTab] = useState<'adhoc' | 'utilities'>('adhoc')
  return (
    <>
      <h1>Expenses</h1>
      <p className="sub">
        Ad hoc costs (with receipts) and recurring utilities (bills, auto-apportioned).
      </p>
      <div className="tabs">
        <button className={`tab ${tab === 'adhoc' ? 'active' : ''}`} onClick={() => setTab('adhoc')}>
          Ad hoc expenses
        </button>
        <button className={`tab ${tab === 'utilities' ? 'active' : ''}`} onClick={() => setTab('utilities')}>
          Utilities
        </button>
      </div>
      {tab === 'adhoc' ? <AdhocExpenses /> : <Utilities />}
    </>
  )
}

// ---------------------------------------------------------------------------
// Ad hoc expenses
// ---------------------------------------------------------------------------
function AdhocExpenses() {
  const { selected } = useProperties()
  const [categories, setCategories] = useState<Category[]>([])
  const [items, setItems] = useState<Expense[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [form, setForm] = useState({
    category: '',
    date: new Date().toISOString().slice(0, 10),
    vendor: '',
    description: '',
    amount: '',
    gst_amount: '',
    apportionment: 'none',
    apportionment_pct: '',
    paid: true,
  })

  const load = async () => {
    if (!selected) return
    setItems(await fetchList<Expense>(`/api/expenses/?kind=adhoc&property=${selected.id}`))
  }

  const loadCategories = async () => setCategories(await fetchList<Category>('/api/categories/'))

  useEffect(() => {
    loadCategories()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id])

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selected) return
    setError('')
    setNotice('')
    const payload: Record<string, unknown> = {
      property: selected.id,
      category: Number(form.category),
      kind: 'adhoc',
      date: form.date,
      vendor: form.vendor,
      description: form.description,
      amount: form.amount || '0',
      gst_amount: form.gst_amount || '0',
      apportionment: form.apportionment,
      paid: form.paid,
    }
    if (form.apportionment === 'custom') payload.apportionment_pct = form.apportionment_pct || '0'
    if (!form.category) {
      setError('Pick a category (create one under the Utilities tab if needed).')
      return
    }
    try {
      await api('/api/expenses/', { method: 'POST', body: JSON.stringify(payload) })
      setForm({ ...form, vendor: '', description: '', amount: '', gst_amount: '' })
      setNotice('Expense added.')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }

  return (
    <>
      {!selected ? <Alert kind="info">Create a property first.</Alert> : null}

      <form className="panel" onSubmit={save}>
        <h2>Add an ad hoc expense</h2>
        <div className="grid">
          <Field label="Category">
            <CategoryPicker
              value={form.category}
              onChange={(id) => setForm({ ...form, category: id === '' ? '' : String(id) })}
              categories={categories}
              onChanged={loadCategories}
              defaultKind="operating"
            />
          </Field>
          <Field label="Date">
            <input type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} />
          </Field>
          <Field label="Vendor">
            <input value={form.vendor} onChange={(e) => setForm({ ...form, vendor: e.target.value })} />
          </Field>
          <Field label="Description">
            <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </Field>
          <Field label="Amount (inc GST)">
            <input type="number" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
          </Field>
          <Field label="GST included">
            <input type="number" step="0.01" value={form.gst_amount} onChange={(e) => setForm({ ...form, gst_amount: e.target.value })} />
          </Field>
          <Field label="Apportionment">
            <select value={form.apportionment} onChange={(e) => setForm({ ...form, apportionment: e.target.value })}>
              <option value="none">Fully deductible</option>
              <option value="area">By floor area of the let</option>
              <option value="custom">Custom %</option>
            </select>
          </Field>
          {form.apportionment === 'custom' ? (
            <Field label="Custom share (0–1)">
              <input type="number" step="0.0001" value={form.apportionment_pct} onChange={(e) => setForm({ ...form, apportionment_pct: e.target.value })} />
            </Field>
          ) : null}
          <Field label="Paid">
            <select value={String(form.paid)} onChange={(e) => setForm({ ...form, paid: e.target.value === 'true' })}>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </Field>
        </div>
        <div style={{ height: 12 }} />
        <button disabled={!selected}>Add expense</button>
        {notice ? <Alert kind="ok">{notice}</Alert> : null}
        {error ? <Alert kind="err">{error}</Alert> : null}
      </form>

      <div className="panel">
        <h2>Ad hoc expenses</h2>
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Category</th>
              <th>Description</th>
              <th className="num">Amount</th>
              <th>Apportionment</th>
              <th className="num">Claimable</th>
              <th>Receipt</th>
            </tr>
          </thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.id}>
                <td>{e.date}</td>
                <td>{e.category_name}</td>
                <td>{e.description || e.vendor || '—'}</td>
                <td className="num">{money(e.amount)}</td>
                <td>{APPORTION_LABELS[e.apportionment] ?? e.apportionment}</td>
                <td className="num">{money(e.deductible_amount)}</td>
                <td>
                  <ReceiptCell expense={e} onUploaded={load} />
                </td>
              </tr>
            ))}
            {items.length === 0 ? (
              <tr>
                <td colSpan={7} className="muted">
                  Nothing yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </>
  )
}

function ReceiptCell({ expense, onUploaded }: { expense: Expense; onUploaded: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const upload = async (file: File) => {
    setBusy(true)
    setErr('')
    const body = new FormData()
    body.append('file', file)
    body.append('property', String(expense.property))
    body.append('expense', String(expense.id))
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
      {expense.receipts.map((r: Receipt) => (
        <a key={r.id} href={r.url} target="_blank" rel="noreferrer">
          📎
        </a>
      ))}
      <input
        ref={inputRef}
        type="file"
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

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function Utilities() {
  const { selected } = useProperties()
  const [categories, setCategories] = useState<Category[]>([])
  const [types, setTypes] = useState<UtilityType[]>([])
  const [typeId, setTypeId] = useState<number | null>(null)
  const [bills, setBills] = useState<UtilityBill[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const billFile = useRef<File | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const [newType, setNewType] = useState({
    name: '',
    category: '',
    frequency: 'quarterly',
    supplier: '',
    apportionment: 'area',
  })
  const [bill, setBill] = useState({
    bill_date: new Date().toISOString().slice(0, 10),
    period_start: '',
    period_end: '',
    amount: '',
    gst_amount: '',
    paid: true,
  })

  const loadTypes = async () => {
    if (!selected) return
    const list = await fetchList<UtilityType>(`/api/utility-types/?property=${selected.id}`)
    setTypes(list)
    setTypeId(list[0]?.id ?? null)
  }
  const loadBills = async (id: number | null) => {
    if (!id) {
      setBills([])
      return
    }
    setBills(await fetchList<UtilityBill>(`/api/utility-bills/?utility_type=${id}`))
  }

  const loadCategories = async () => setCategories(await fetchList<Category>('/api/categories/'))

  useEffect(() => {
    loadCategories()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    loadTypes()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id])
  useEffect(() => {
    loadBills(typeId)
  }, [typeId])

  const addType = async () => {
    if (!selected || !newType.name.trim() || !newType.category) {
      setError('Utility type needs a name and a category.')
      return
    }
    setError('')
    await api('/api/utility-types/', {
      method: 'POST',
      body: JSON.stringify({ ...newType, property: selected.id, category: Number(newType.category) }),
    })
    setNewType({ ...newType, name: '', supplier: '' })
    await loadTypes()
  }

  const readWithAI = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) {
      setError('Choose a bill file first.')
      return
    }
    setError('')
    setNotice('')
    billFile.current = file
    const body = new FormData()
    body.append('file', file)
    try {
      const data = await api<BillExtraction>('/api/utilities/extract/', { method: 'POST', body })
      if (data.needs_ocr) {
        setError(data.message ?? 'This bill has no text layer — enter the amount manually.')
        return
      }
      setBill((prev) => ({
        ...prev,
        amount: data.amount ?? prev.amount,
        gst_amount: data.gst_amount ?? prev.gst_amount,
        bill_date: data.bill_date ?? prev.bill_date,
        period_start: data.period_start ?? prev.period_start,
        period_end: data.period_end ?? prev.period_end,
      }))
      setNotice(`Read with ${data.extracted_by ?? 'AI'} — please double-check the values.`)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Extraction failed'
      setError(`${message} — you can still enter the amount manually.`)
    }
  }

  const addBill = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!typeId) {
      setError('Add/select a utility type first.')
      return
    }
    setError('')
    setNotice('')
    const body = new FormData()
    body.append('utility_type', String(typeId))
    body.append('bill_date', bill.bill_date)
    if (bill.period_start) body.append('period_start', bill.period_start)
    if (bill.period_end) body.append('period_end', bill.period_end)
    body.append('amount', bill.amount || '0')
    body.append('gst_amount', bill.gst_amount || '0')
    body.append('paid', String(bill.paid))
    if (billFile.current) body.append('attachment', billFile.current)
    try {
      await api('/api/utility-bills/', { method: 'POST', body })
      setNotice('Bill added — claimable amount calculated from the let share.')
      setBill({ ...bill, amount: '', gst_amount: '', period_start: '', period_end: '' })
      billFile.current = null
      if (fileRef.current) fileRef.current.value = ''
      await loadBills(typeId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }

  const claimTotal = bills.reduce((s, b) => s + Number(b.claimable_amount ?? 0), 0)
  const amountTotal = bills.reduce((s, b) => s + Number(b.amount), 0)

  return (
    <>
      {!selected ? <Alert kind="info">Create a property first.</Alert> : null}
      {notice ? <Alert kind="ok">{notice}</Alert> : null}
      {error ? <Alert kind="err">{error}</Alert> : null}

      <div className="panel">
        <h2>Utility types</h2>
        <p className="sub">
          Add each utility you receive (electricity, water, internet…), how often the
          bill arrives, and how it's apportioned.
        </p>
        <table>
          <thead>
            <tr>
              <th>Utility</th>
              <th>Category</th>
              <th>Frequency</th>
              <th>Supplier</th>
              <th>Apportionment</th>
            </tr>
          </thead>
          <tbody>
            {types.map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td>
                <td>{t.category_name}</td>
                <td>{t.frequency}</td>
                <td>{t.supplier || '—'}</td>
                <td>{APPORTION_LABELS[t.apportionment] ?? t.apportionment}</td>
              </tr>
            ))}
            {types.length === 0 ? (
              <tr>
                <td colSpan={5} className="muted">
                  No utility types yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>

        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <label>Name</label>
            <input value={newType.name} onChange={(e) => setNewType({ ...newType, name: e.target.value })} />
          </div>
          <div>
            <label>Category</label>
            <CategoryPicker
              value={newType.category}
              onChange={(id) => setNewType({ ...newType, category: id === '' ? '' : String(id) })}
              categories={categories}
              onChanged={loadCategories}
              defaultKind="utility"
              preferKinds={['utility']}
            />
          </div>
          <div>
            <label>Frequency</label>
            <select value={newType.frequency} onChange={(e) => setNewType({ ...newType, frequency: e.target.value })}>
              <option value="monthly">Monthly</option>
              <option value="quarterly">Quarterly</option>
              <option value="half_yearly">Half-yearly</option>
              <option value="yearly">Yearly</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div>
            <label>Supplier</label>
            <input value={newType.supplier} onChange={(e) => setNewType({ ...newType, supplier: e.target.value })} />
          </div>
          <div>
            <label>Apportionment</label>
            <select value={newType.apportionment} onChange={(e) => setNewType({ ...newType, apportionment: e.target.value })}>
              <option value="area">By floor area of the let</option>
              <option value="none">Fully deductible</option>
            </select>
          </div>
          <button type="button" className="ghost small" onClick={addType}>
            + Add utility type
          </button>
        </div>

        <p className="hint" style={{ marginTop: 8 }}>
          Tip: pick <strong>“+ Add new category…”</strong> in the Category dropdown to create one
          without leaving this page.
        </p>
      </div>

      <form className="panel" onSubmit={addBill}>
        <h2>Add a bill</h2>
        <div className="row">
          <div>
            <label>Utility type</label>
            <select value={typeId ?? ''} onChange={(e) => setTypeId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">— select —</option>
              {types.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.frequency})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Bill PDF / image</label>
            <input type="file" accept="application/pdf,image/*" ref={fileRef} />
          </div>
          <button type="button" className="ghost" onClick={readWithAI}>
            ✨ Read bill with AI
          </button>
        </div>

        <div className="grid" style={{ marginTop: 12 }}>
          <Field label="Bill date">
            <input type="date" value={bill.bill_date} onChange={(e) => setBill({ ...bill, bill_date: e.target.value })} />
          </Field>
          <Field label="Period start">
            <input type="date" value={bill.period_start} onChange={(e) => setBill({ ...bill, period_start: e.target.value })} />
          </Field>
          <Field label="Period end">
            <input type="date" value={bill.period_end} onChange={(e) => setBill({ ...bill, period_end: e.target.value })} />
          </Field>
          <Field label="Amount">
            <input type="number" step="0.01" value={bill.amount} onChange={(e) => setBill({ ...bill, amount: e.target.value })} />
          </Field>
          <Field label="GST included">
            <input type="number" step="0.01" value={bill.gst_amount} onChange={(e) => setBill({ ...bill, gst_amount: e.target.value })} />
          </Field>
          <Field label="Paid">
            <select value={String(bill.paid)} onChange={(e) => setBill({ ...bill, paid: e.target.value === 'true' })}>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </Field>
        </div>
        <div style={{ height: 12 }} />
        <button disabled={!typeId}>Save bill</button>
      </form>

      <div className="panel">
        <h2>Bills</h2>
        <table>
          <thead>
            <tr>
              <th>Bill date</th>
              <th>Period</th>
              <th className="num">Amount</th>
              <th className="num">Claimable</th>
              <th>Bill</th>
            </tr>
          </thead>
          <tbody>
            {bills.map((b) => (
              <tr key={b.id}>
                <td>{b.bill_date ?? '—'}</td>
                <td>
                  {b.period_start ?? '?'} → {b.period_end ?? '?'}
                </td>
                <td className="num">{money(b.amount)}</td>
                <td className="num">{money(b.claimable_amount)}</td>
                <td>
                  {b.attachment_url ? (
                    <a href={b.attachment_url} target="_blank" rel="noreferrer">
                      📎
                    </a>
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            ))}
            {bills.length === 0 ? (
              <tr>
                <td colSpan={5} className="muted">
                  No bills yet.
                </td>
              </tr>
            ) : null}
          </tbody>
          {bills.length ? (
            <tfoot>
              <tr>
                <td colSpan={2}>Total</td>
                <td className="num">{money(amountTotal)}</td>
                <td className="num">{money(claimTotal)}</td>
                <td />
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>
    </>
  )
}
