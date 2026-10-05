import { useEffect, useRef, useState } from 'react'
import { Alert } from './components'

// Largest dimension we keep when normalising a captured photo.
export const MAX_W = 1000
export const MAX_H = 1600

export function canvasToBlob(
  canvas: HTMLCanvasElement,
  type = 'image/jpeg',
  quality = 0.85,
): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('Could not export the photo'))),
      type,
      quality,
    )
  })
}

/** An in-app camera using getUserMedia — no hand-off to the camera app, so it
 *  dodges the Android "unable to complete previous operation due to low
 *  memory" bug. Only available in a secure context (HTTPS or localhost). */
export function CameraCapture({
  onCapture,
  onClose,
}: {
  onCapture: (blob: Blob) => void
  onClose: () => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let cancelled = false
    navigator.mediaDevices
      .getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 2560 } },
        audio: false,
      })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          videoRef.current.play().catch(() => {})
        }
      })
      .catch(() => setErr('Could not open the camera.'))
    return () => {
      cancelled = true
      streamRef.current?.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
  }, [])

  const shoot = async () => {
    const v = videoRef.current
    if (!v || !v.videoWidth) return
    const scale = Math.min(1, MAX_W / v.videoWidth, MAX_H / v.videoHeight)
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(v.videoWidth * scale)
    canvas.height = Math.round(v.videoHeight * scale)
    canvas.getContext('2d')!.drawImage(v, 0, 0, canvas.width, canvas.height)
    onCapture(await canvasToBlob(canvas))
  }

  return (
    <div className="cam-modal">
      <video ref={videoRef} className="cam-video" autoPlay playsInline muted />
      <div className="row" style={{ marginTop: 10 }}>
        <button type="button" onClick={shoot} disabled={Boolean(err)}>
          Capture
        </button>
        <button type="button" className="ghost" onClick={onClose}>
          Cancel
        </button>
      </div>
      {err ? <Alert kind="err">{err} Use “Choose photo” instead.</Alert> : null}
    </div>
  )
}

/**
 * A "take a photo / choose a photo" pair for forms that attach an image.
 *
 * On a phone the camera goes through the OS camera app via
 * `capture="environment"`; when the page is served over HTTPS we offer an
 * in-app camera instead (which avoids the Android camera-hand-off low-memory
 * bug). Both paths hand a plain `File` back to the caller.
 */
export function PhotoPicker({
  onPick,
  disabled = false,
  takeLabel = '📷 Take photo',
  chooseLabel = '🖼️ Choose photo',
  hint,
}: {
  onPick: (file: File) => void
  disabled?: boolean
  takeLabel?: string
  chooseLabel?: string
  hint?: string
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const shootRef = useRef<HTMLInputElement>(null)
  const [cameraOn, setCameraOn] = useState(false)
  const cameraSupported =
    typeof navigator !== 'undefined' &&
    typeof window !== 'undefined' &&
    window.isSecureContext &&
    Boolean(navigator.mediaDevices?.getUserMedia)

  return (
    <>
      <input
        type="file"
        accept="image/*"
        ref={fileRef}
        disabled={disabled}
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onPick(f)
          e.target.value = ''
        }}
      />
      <input
        type="file"
        accept="image/*"
        capture="environment"
        ref={shootRef}
        disabled={disabled}
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onPick(f)
          e.target.value = ''
        }}
      />
      <div className="row">
        {cameraSupported ? (
          <button type="button" className="ghost" disabled={disabled} onClick={() => setCameraOn(true)}>
            📷 Open camera
          </button>
        ) : (
          <button
            type="button"
            className="ghost"
            disabled={disabled}
            onClick={() => shootRef.current?.click()}
          >
            {takeLabel}
          </button>
        )}
        <button
          type="button"
          className="ghost"
          disabled={disabled}
          onClick={() => fileRef.current?.click()}
        >
          {chooseLabel}
        </button>
      </div>
      {cameraOn ? (
        <CameraCapture
          onCapture={(blob) => {
            setCameraOn(false)
            onPick(new File([blob], 'photo.jpg', { type: 'image/jpeg' }))
          }}
          onClose={() => setCameraOn(false)}
        />
      ) : null}
      {hint ? <div className="hint">{hint}</div> : null}
    </>
  )
}
