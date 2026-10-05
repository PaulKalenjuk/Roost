import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react'
import {
  api,
  fetchList,
  money,
  type AllocationResult,
  type Category,
  type HighlightBox,
  type UnallocatedExpense,
} from '../api'
import { Alert, CategoryPicker, Field } from '../components'
import { CameraCapture, MAX_H, MAX_W, canvasToBlob } from '../photo'
import { useProperties } from '../store'

const MIN_BOX = 0.004

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('Could not load the photo'))
    img.src = src
  })
}

/** Downscale a picked photo during decode so a 48MP phone shot can't exhaust
 *  memory, and normalise everything to a right-sized JPEG object URL. */
async function prepareFromFile(file: File): Promise<string> {
  try {
    if (typeof createImageBitmap === 'function') {
      const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' })
      const scale = Math.min(1, MAX_W / bmp.width, MAX_H / bmp.height)
      const w = Math.max(1, Math.round(bmp.width * scale))
      const h = Math.max(1, Math.round(bmp.height * scale))
      const canvas = document.createElement('canvas')
      canvas.width = w
      canvas.height = h
      canvas.getContext('2d')!.drawImage(bmp, 0, 0, w, h)
      if (typeof bmp.close === 'function') bmp.close()
      return URL.createObjectURL(await canvasToBlob(canvas))
    }
  } catch {
    /* fall back to the raw file */
  }
  return URL.createObjectURL(file)
}

export interface HighlightEditorHandle {
  hasImage: () => boolean
  exportBlob: () => Promise<Blob | null>
}

/**
 * A photo with finger/mouse-drawn highlight boxes. Boxes are reported in
 * normalised (0..1) coordinates so they survive any display size, and the
 * exported image is the *clean* photo (boxes are drawn from the coordinates on
 * the server's copy, so highlights never get baked in).
 */
const HighlightEditor = forwardRef<
  HighlightEditorHandle,
  { imageSrc: string | null; boxes: HighlightBox[]; onChange: (boxes: HighlightBox[]) => void }
>(function HighlightEditor({ imageSrc, boxes, onChange }, ref) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imgElRef = useRef<HTMLImageElement | null>(null)
  const dragRef = useRef<{ x0: number; y0: number; x1: number; y1: number } | null>(null)
  const boxesRef = useRef(boxes)
  boxesRef.current = boxes

  const paint = useCallback(() => {
    const canvas = canvasRef.current
    const img = imgElRef.current
    if (!canvas || !img) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    const drawBox = (b: HighlightBox, preview: boolean) => {
      const x = b.x * canvas.width
      const y = b.y * canvas.height
      const w = b.w * canvas.width
      const h = b.h * canvas.height
      ctx.fillStyle = 'rgba(255, 205, 0, 0.30)'
      ctx.fillRect(x, y, w, h)
      ctx.lineWidth = 2
      ctx.strokeStyle = preview ? '#b54708' : '#d99f00'
      if (preview) ctx.setLineDash([6, 4])
      ctx.strokeRect(x, y, w, h)
      ctx.setLineDash([])
    }
    boxesRef.current.forEach((b) => drawBox(b, false))
    const drag = dragRef.current
    if (drag && canvas.width && canvas.height) {
      drawBox(
        {
          x: Math.min(drag.x0, drag.x1) / canvas.width,
          y: Math.min(drag.y0, drag.y1) / canvas.height,
          w: Math.abs(drag.x1 - drag.x0) / canvas.width,
          h: Math.abs(drag.y1 - drag.y0) / canvas.height,
        },
        true,
      )
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    if (!imageSrc) {
      imgElRef.current = null
      const canvas = canvasRef.current
      if (canvas) {
        canvas.width = 0
        canvas.height = 0
      }
      return
    }
    loadImage(imageSrc)
      .then((img) => {
        if (cancelled) return
        imgElRef.current = img
        const canvas = canvasRef.current
        if (!canvas) return
        const scale = Math.min(1, MAX_W / img.naturalWidth, MAX_H / img.naturalHeight)
        canvas.width = Math.max(1, Math.round(img.naturalWidth * scale))
        canvas.height = Math.max(1, Math.round(img.naturalHeight * scale))
        paint()
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [imageSrc, paint])

  useEffect(() => {
    paint()
  }, [boxes, paint])

  useImperativeHandle(
    ref,
    () => ({
      hasImage: () => Boolean(imgElRef.current),
      exportBlob: async () => {
        const canvas = canvasRef.current
        const img = imgElRef.current
        if (!canvas || !img) return null
        const out = document.createElement('canvas')
        out.width = canvas.width
        out.height = canvas.height
        const ctx = out.getContext('2d')
        if (!ctx) return null
        ctx.drawImage(img, 0, 0, out.width, out.height)
        return await canvasToBlob(out)
      },
    }),
    [],
  )

  const pos = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current!
    const rect = canvas.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * canvas.width
    const y = ((e.clientY - rect.top) / rect.height) * canvas.height
    return {
      x: Math.max(0, Math.min(x, canvas.width)),
      y: Math.max(0, Math.min(y, canvas.height)),
    }
  }

  return (
    <div className="capture">
      <canvas
        ref={canvasRef}
        className={`highlight-canvas${imageSrc ? '' : ' empty'}`}
        onPointerDown={(e) => {
          if (!imgElRef.current) return
          e.currentTarget.setPointerCapture(e.pointerId)
          const p = pos(e)
          dragRef.current = { x0: p.x, y0: p.y, x1: p.x, y1: p.y }
          paint()
        }}
        onPointerMove={(e) => {
          if (!dragRef.current) return
          const p = pos(e)
          dragRef.current.x1 = p.x
          dragRef.current.y1 = p.y
          paint()
        }}
        onPointerUp={(e) => {
          const canvas = canvasRef.current
          const drag = dragRef.current
          dragRef.current = null
          if (!canvas || !drag) return
          const x = Math.min(drag.x0, drag.x1) / canvas.width
          const y = Math.min(drag.y0, drag.y1) / canvas.height
          const w = Math.abs(drag.x1 - drag.x0) / canvas.width
          const h = Math.abs(drag.y1 - drag.y0) / canvas.height
          if (w >= MIN_BOX && h >= MIN_BOX) {
            onChange([...boxesRef.current, { x, y, w, h }])
          } else {
            paint()
          }
        }}
        onPointerCancel={() => {
          dragRef.current = null
          paint()
        }}
      />
      {!imageSrc ? (
        <p className="muted" style={{ marginTop: 10 }}>
          Take or choose a photo, then drag over the lines you want included.
        </p>
      ) : null}
    </div>
  )
})

export default function Receipts() {
  const { selected } = useProperties()
  const [categories, setCategories] = useState<Category[]>([])
  const [items, setItems] = useState<UnallocatedExpense[]>([])
  const [imageSrc, setImageSrc] = useState<string | null>(null)
  const [boxes, setBoxes] = useState<HighlightBox[]>([])
  const [editingId, setEditingId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [saving, setSaving] = useState(false)

  const fileRef = useRef<HTMLInputElement>(null)
  const shootRef = useRef<HTMLInputElement>(null)
  const editorRef = useRef<HighlightEditorHandle>(null)
  const objectUrlRef = useRef<string | null>(null)
  const [cameraOn, setCameraOn] = useState(false)
  const cameraSupported =
    typeof navigator !== 'undefined' &&
    typeof window !== 'undefined' &&
    window.isSecureContext &&
    Boolean(navigator.mediaDevices?.getUserMedia)

  const loadCategories = async () => setCategories(await fetchList<Category>('/api/categories/'))
  const loadItems = async () => {
    if (!selected) return
    const list = await fetchList<UnallocatedExpense>(
      `/api/unallocated/?property=${selected.id}`,
    )
    setItems(list.filter((i) => i.status !== 'allocated'))
  }

  useEffect(() => {
    loadCategories()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    loadItems()
    resetEditor()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id])

  const resetEditor = () => {
    setImageSrc(null)
    setBoxes([])
    setEditingId(null)
    setCameraOn(false)
    if (fileRef.current) fileRef.current.value = ''
    if (shootRef.current) shootRef.current.value = ''
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current)
      objectUrlRef.current = null
    }
  }

  const setSource = (url: string) => {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current)
    objectUrlRef.current = url
    setBoxes([])
    setEditingId(null)
    setImageSrc(url)
  }

  const onPickFile = async (file: File) => {
    setError('')
    setNotice('')
    try {
      setSource(await prepareFromFile(file))
    } catch {
      setError('Could not read that photo — try another one, or a smaller image.')
    }
  }

  const onCaptured = async (blob: Blob) => {
    setCameraOn(false)
    setSource(URL.createObjectURL(blob))
  }

  const startEdit = (item: UnallocatedExpense) => {
    setError('')
    setNotice('')
    if (!item.image_url) {
      setError('That capture has no image to edit.')
      return
    }
    setEditingId(item.id)
    setBoxes(item.highlights ?? [])
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current)
      objectUrlRef.current = null
    }
    setImageSrc(item.image_url)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const save = async () => {
    if (!selected) return
    if (!editorRef.current?.hasImage()) {
      setError('Take or choose a photo first.')
      return
    }
    setError('')
    setNotice('')
    setSaving(true)
    try {
      if (editingId) {
        await api(`/api/unallocated/${editingId}/`, {
          method: 'PATCH',
          body: JSON.stringify({ highlights: boxes }),
        })
        setNotice('Highlights updated — process it again when you are ready.')
      } else {
        const blob = await editorRef.current.exportBlob()
        if (!blob) throw new Error('Could not read the photo')
        const body = new FormData()
        body.append('property', String(selected.id))
        body.append('image', blob, 'receipt.jpg')
        body.append('highlights', JSON.stringify(boxes))
        await api('/api/unallocated/', { method: 'POST', body })
        setNotice(
          boxes.length
            ? `Saved to unallocated with ${boxes.length} highlight box${boxes.length > 1 ? 'es' : ''}.`
            : 'Saved to unallocated (whole receipt — no boxes drawn).',
        )
      }
      resetEditor()
      await loadItems()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <h1>Receipts</h1>
      <p className="sub">
        Snap a receipt, highlight the lines that belong to your expense, and save it to the
        unallocated pile. Then let AI read the highlighted lines and suggest a category before
        you file it.
      </p>

      {!selected ? <Alert kind="info">Create a property first.</Alert> : null}
      {notice ? <Alert kind="ok">{notice}</Alert> : null}
      {error ? <Alert kind="err">{error}</Alert> : null}

      <div className="panel">
        <h2>{editingId ? 'Edit highlights' : 'Capture a receipt'}</h2>
        <div className="row">
          <div>
            <label>Photo</label>
            <input
              type="file"
              accept="image/*"
              ref={fileRef}
              disabled={!selected}
              style={{ display: 'none' }}
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) void onPickFile(f)
                e.target.value = ''
              }}
            />
            <input
              type="file"
              accept="image/*"
              capture="environment"
              ref={shootRef}
              disabled={!selected}
              style={{ display: 'none' }}
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) void onPickFile(f)
                e.target.value = ''
              }}
            />
            {cameraSupported ? (
              <button type="button" disabled={!selected} onClick={() => setCameraOn(true)}>
                📷 Open camera
              </button>
            ) : (
              <button
                type="button"
                className="ghost"
                disabled={!selected}
                onClick={() => shootRef.current?.click()}
              >
                📷 Take photo
              </button>
            )}
            <button
              type="button"
              className="ghost"
              disabled={!selected}
              onClick={() => fileRef.current?.click()}
            >
              🖼️ Choose photo
            </button>
          </div>
          <button
            type="button"
            className="ghost small"
            disabled={!imageSrc || boxes.length === 0}
            onClick={() => setBoxes(boxes.slice(0, -1))}
          >
            Undo last box
          </button>
          <button
            type="button"
            className="ghost small"
            disabled={boxes.length === 0}
            onClick={() => setBoxes([])}
          >
            Clear boxes
          </button>
          {imageSrc ? (
            <button type="button" className="ghost small" onClick={resetEditor}>
              Cancel
            </button>
          ) : null}
        </div>

        {!imageSrc ? (
          <p className="hint" style={{ marginTop: 10 }}>
            If “Take photo” shows a low-memory error on your phone, use{' '}
            <strong>Choose photo</strong> to pick from your gallery instead (the
            camera-app hand-off is a known Android/Chrome quirk).
          </p>
        ) : null}

        {cameraOn ? (
          <CameraCapture
            onCapture={(blob) => void onCaptured(blob)}
            onClose={() => setCameraOn(false)}
          />
        ) : null}

        <HighlightEditor ref={editorRef} imageSrc={imageSrc} boxes={boxes} onChange={setBoxes} />
        {imageSrc ? (
          <p className="hint">
            {boxes.length
              ? `${boxes.length} highlight box${boxes.length > 1 ? 'es' : ''} — drag over each line you want counted, or over the whole block.`
              : 'Drag over the lines you want the AI to read.'}{' '}
            Tip: highlight the individual line items, not the receipt total.
          </p>
        ) : null}

        <div style={{ height: 12 }} />
        <div className="row">
          <button type="button" onClick={save} disabled={saving || !selected || !imageSrc}>
            {saving ? 'Saving…' : editingId ? 'Update highlights' : 'Save to unallocated'}
          </button>
        </div>
      </div>

      <div className="panel">
        <h2>Unallocated receipts</h2>
        <p className="sub">
          Process each one with AI to add up the highlighted lines and guess the category, then
          file it as a real expense.
        </p>
        {items.length === 0 ? (
          <p className="muted">Nothing waiting. Snap a receipt above to get started.</p>
        ) : (
          <div className="cards">
            {items.map((item) => (
              <UnallocatedCard
                key={item.id}
                item={item}
                categories={categories}
                onReload={loadItems}
                onCategoriesChanged={loadCategories}
                onEditHighlights={startEdit}
              />
            ))}
          </div>
        )}
      </div>
    </>
  )
}

function UnallocatedCard({
  item,
  categories,
  onReload,
  onCategoriesChanged,
  onEditHighlights,
}: {
  item: UnallocatedExpense
  categories: Category[]
  onReload: () => Promise<void>
  onCategoriesChanged: () => Promise<void>
  onEditHighlights: (item: UnallocatedExpense) => void
}) {
  const [processing, setProcessing] = useState(false)
  const [allocating, setAllocating] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [form, setForm] = useState({
    category: item.category ? String(item.category) : '',
    amount: item.amount ?? '',
    gst_amount: item.gst_amount ?? '',
    date: item.date ?? '',
    vendor: item.vendor ?? '',
    description: item.description ?? '',
    apportionment: 'none',
  })

  // Keep the allocate form in step when the item is (re)processed.
  useEffect(() => {
    setForm((f) => ({
      ...f,
      category: item.category ? String(item.category) : f.category,
      amount: item.amount ?? f.amount,
      gst_amount: item.gst_amount ?? f.gst_amount,
      vendor: item.vendor || f.vendor,
      date: item.date || f.date,
    }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.id, item.amount, item.category, item.status])

  const process = async () => {
    setProcessing(true)
    setError('')
    setNotice('')
    try {
      const updated = await api<UnallocatedExpense>(`/api/unallocated/${item.id}/process/`, {
        method: 'POST',
      })
      setNotice(
        updated.amount
          ? `Read the highlighted lines — total ${money(updated.amount)}.`
          : 'Read the receipt, but found no line amounts. Check the highlights.',
      )
      await onReload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Processing failed')
    } finally {
      setProcessing(false)
    }
  }

  const allocate = async () => {
    if (!form.category) {
      setError('Pick a category.')
      return
    }
    setAllocating(true)
    setError('')
    setNotice('')
    try {
      const result = await api<AllocationResult>(`/api/unallocated/${item.id}/allocate/`, {
        method: 'POST',
        body: JSON.stringify({
          category: Number(form.category),
          amount: form.amount || item.amount || '0',
          gst_amount: form.gst_amount || '0',
          date: form.date,
          vendor: form.vendor,
          description: form.description,
          apportionment: form.apportionment,
        }),
      })
      setNotice(`Filed as a ${money(result.expense.amount)} expense.`)
      await onReload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not allocate')
    } finally {
      setAllocating(false)
    }
  }

  const remove = async () => {
    if (!window.confirm('Delete this captured receipt?')) return
    setError('')
    try {
      await api(`/api/unallocated/${item.id}/`, { method: 'DELETE' })
      await onReload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  const lines = item.extracted?.lines as
    | { description: string | null; amount: string | null }[]
    | undefined

  return (
    <div className="card">
      <div className="row" style={{ alignItems: 'flex-start', gap: 12 }}>
        {item.image_url ? (
          <a href={item.image_url} target="_blank" rel="noreferrer">
            <img src={item.image_url} alt="Receipt" className="capture-thumb" />
          </a>
        ) : null}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="row" style={{ gap: 6 }}>
            <span className={`badge ${item.status === 'processed' ? 'ok' : 'warn'}`}>
              {item.status === 'processed' ? 'read with AI' : 'pending'}
            </span>
            <span className="muted">{item.date}</span>
          </div>
          <div style={{ marginTop: 6 }}>
            <strong>{item.vendor || 'Receipt'}</strong>
          </div>
          <div className="muted" style={{ fontSize: 13 }}>
            {item.highlight_count
              ? `${item.highlight_count} highlight box${item.highlight_count > 1 ? 'es' : ''}`
              : 'whole receipt'}
          </div>
          {item.amount ? (
            <div style={{ marginTop: 6 }} className="totals">
              {money(item.amount)}
              {item.category_name ? <span className="muted"> · {item.category_name}</span> : null}
            </div>
          ) : null}
        </div>
      </div>

      {lines && lines.length ? (
        <div style={{ marginTop: 10 }}>
          <table>
            <thead>
              <tr>
                <th>Line read</th>
                <th className="num">Amount</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={i}>
                  <td>{l.description || '—'}</td>
                  <td className="num">{money(l.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {item.rationale ? <p className="working">Category guess: {item.rationale}</p> : null}
        </div>
      ) : null}

      <div className="row" style={{ marginTop: 12 }}>
        <button
          type="button"
          className="ghost small btn-busy"
          onClick={process}
          disabled={processing}
        >
          {processing ? (
            <>
              <span className="spinner" />
              Processing…
            </>
          ) : (
            '✨ Process with AI'
          )}
        </button>
        <button type="button" className="ghost small" onClick={() => onEditHighlights(item)}>
          Edit highlights
        </button>
        <button type="button" className="ghost small" onClick={remove}>
          Delete
        </button>
      </div>

      {item.status === 'processed' || item.amount ? (
        <div style={{ marginTop: 12 }}>
          <div className="grid">
            <Field label="Category">
              <CategoryPicker
                value={form.category}
                onChange={(id) => setForm({ ...form, category: id === '' ? '' : String(id) })}
                categories={categories}
                onChanged={onCategoriesChanged}
                defaultKind="operating"
              />
            </Field>
            <Field label="Amount (inc GST)">
              <input
                type="number"
                step="0.01"
                value={form.amount}
                onChange={(e) => setForm({ ...form, amount: e.target.value })}
              />
            </Field>
            <Field label="GST included">
              <input
                type="number"
                step="0.01"
                value={form.gst_amount}
                onChange={(e) => setForm({ ...form, gst_amount: e.target.value })}
              />
            </Field>
            <Field label="Date">
              <input
                type="date"
                value={form.date}
                onChange={(e) => setForm({ ...form, date: e.target.value })}
              />
            </Field>
            <Field label="Vendor">
              <input
                value={form.vendor}
                onChange={(e) => setForm({ ...form, vendor: e.target.value })}
              />
            </Field>
            <Field label="Description">
              <input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </Field>
            <Field label="Apportionment">
              <select
                value={form.apportionment}
                onChange={(e) => setForm({ ...form, apportionment: e.target.value })}
              >
                <option value="none">Fully deductible</option>
                <option value="area">By floor area of the let</option>
                <option value="area_nights">By floor area × nights booked</option>
              </select>
            </Field>
          </div>
          <div style={{ height: 10 }} />
          <button type="button" className="small" onClick={allocate} disabled={allocating}>
            {allocating ? 'Filing…' : 'Add to expenses'}
          </button>
        </div>
      ) : null}

      {notice ? <Alert kind="ok">{notice}</Alert> : null}
      {error ? <Alert kind="err">{error}</Alert> : null}
    </div>
  )
}
