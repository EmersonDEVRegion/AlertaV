import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { REPORT_TEXT_MAX, REPORT_TEXT_MIN } from '@/api/citizenReports'
import { useCitizenReport } from '@/hooks/useCitizenReport'
import { useGeolocation } from '@/hooks/useGeolocation'

interface CitizenReportModalProps {
  onClose: () => void
}

/** Milisegundos que queda visible el acuse antes de cerrarse solo. */
const SUCCESS_DISMISS_MS = 2200

/** Sobre este radio la lectura del GPS ya no describe un lugar, sino un barrio. */
const COARSE_ACCURACY_M = 100

function formatAccuracy(meters: number): string {
  return meters >= 1000
    ? `± ${(meters / 1000).toFixed(1)} km`
    : `± ${Math.round(meters)} m`
}

/**
 * Modal de reporte ciudadano.
 *
 * Este SÍ es modal, al revés que `IncidentSheet`: la persona está describiendo
 * algo que ve, y el mapa detrás solo compite por su atención. Además el envío
 * es una acción con consecuencias — entra al motor de correlación — y merece un
 * foco exclusivo.
 */
export function CitizenReportModal({ onClose }: CitizenReportModalProps) {
  const [text, setText] = useState('')
  const [touched, setTouched] = useState(false)

  const geo = useGeolocation()
  const { submit, isSubmitting, isSuccess, errorMessage, reset } = useCitizenReport()

  const titleId = useId()
  const textareaId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const { request: requestLocation } = geo

  // El GPS se pide al abrir, no al enviar: arrancar el chip toma segundos y esos
  // segundos transcurren mientras la persona escribe, no después.
  useEffect(() => {
    requestLocation()
  }, [requestLocation])

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  // Bloquear el scroll de fondo evita que en teléfono el mapa se desplace
  // debajo del modal cuando el teclado empuja el layout.
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [])

  const close = useCallback(() => {
    // Cerrar en medio del envío dejaría a la persona sin saber si su reporte
    // llegó. El único camino de salida durante el POST es esperar.
    if (isSubmitting) return
    reset()
    onClose()
  }, [isSubmitting, onClose, reset])

  // Acuse breve y cierre automático: en una emergencia nadie quiere quedarse
  // apretando "aceptar".
  useEffect(() => {
    if (!isSuccess) return
    const timer = window.setTimeout(() => {
      reset()
      onClose()
    }, SUCCESS_DISMISS_MS)
    return () => window.clearTimeout(timer)
  }, [isSuccess, onClose, reset])

  // Escape para cerrar y Tab confinado al diálogo: sin esto el foco se escapa a
  // los controles de MapLibre que quedan detrás del velo.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        close()
        return
      }

      if (event.key !== 'Tab') return
      const root = dialogRef.current
      if (!root) return

      const focusables = root.querySelectorAll<HTMLElement>(
        'button:not([disabled]), textarea:not([disabled]), [href], input:not([disabled]), select, [tabindex]:not([tabindex="-1"])',
      )
      if (focusables.length === 0) return

      const first = focusables[0]!
      const last = focusables[focusables.length - 1]!
      const active = document.activeElement

      if (event.shiftKey && active === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    // Fase de captura: el listener de Escape de `IncidentSheet` también escucha
    // en `window`, y un solo Escape no debe cerrar las dos cosas a la vez.
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [close])

  const trimmed = text.trim()
  const textTooShort = trimmed.length < REPORT_TEXT_MIN
  const showTextError = touched && textTooShort
  const canSubmit = geo.status === 'ready' && !!geo.coords && !textTooShort && !isSubmitting

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setTouched(true)
    if (!geo.coords || textTooShort || isSubmitting) return

    try {
      await submit({
        lat: geo.coords.lat,
        lon: geo.coords.lon,
        text: trimmed,
      })
    } catch {
      // `useCitizenReport` ya tradujo el error a `errorMessage`. Este catch solo
      // existe para que el rechazo de la promesa no escale a un unhandled.
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/60 p-0 backdrop-blur-sm sm:items-center sm:p-4"
      onMouseDown={(event) => {
        // `mousedown` y no `click`: soltar el botón fuera del diálogo después de
        // seleccionar texto dentro no debería cerrar el formulario.
        if (event.target === event.currentTarget) close()
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="
          flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-2xl bg-white
          shadow-2xl ring-1 ring-slate-900/10
          sm:max-w-md sm:rounded-2xl
        "
      >
        <header className="flex items-start gap-3 border-b border-slate-200 px-5 py-4">
          <span
            aria-hidden
            className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-full bg-red-100 text-lg"
          >
            🚨
          </span>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-base font-bold text-slate-900">
              Reportar emergencia
            </h2>
            <p className="mt-0.5 text-xs leading-snug text-slate-500">
              Tu reporte entra como una señal más al motor de correlación.
            </p>
          </div>
          <button
            type="button"
            onClick={close}
            disabled={isSubmitting}
            aria-label="Cerrar formulario de reporte"
            className="-mr-1 grid size-9 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-40"
          >
            <span aria-hidden className="text-lg leading-none">✕</span>
          </button>
        </header>

        {isSuccess ? (
          <div
            role="status"
            className="flex flex-col items-center gap-2 px-6 py-10 text-center"
          >
            <span
              aria-hidden
              className="grid size-12 place-items-center rounded-full bg-emerald-100 text-2xl text-emerald-700"
            >
              ✓
            </span>
            <p className="text-base font-bold text-slate-900">Reporte enviado</p>
            <p className="max-w-xs text-xs leading-snug text-slate-600">
              Quedó registrado como señal ciudadana. Si otras fuentes lo
              corroboran, aparecerá en el mapa como incidente en los próximos
              minutos.
            </p>
            <p className="mt-2 text-xs font-semibold text-red-700">
              Si hay riesgo para alguien, llama al 132.
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
            <div className="flex-1 overflow-y-auto px-5 py-4">
              {/* --- Ubicación ------------------------------------------------ */}
              <div className="rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Tu ubicación
                </p>

                {geo.status === 'locating' && (
                  <p className="mt-1.5 flex items-center gap-2 text-sm text-slate-700">
                    <span
                      aria-hidden
                      className="size-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600"
                    />
                    Obteniendo ubicación…
                  </p>
                )}

                {geo.status === 'ready' && geo.coords && (
                  <>
                    <p className="mt-1.5 font-mono text-sm text-slate-800">
                      {geo.coords.lat.toFixed(5)}, {geo.coords.lon.toFixed(5)}
                    </p>
                    <p className="mt-0.5 text-xs text-slate-500">
                      Precisión {formatAccuracy(geo.coords.accuracyM)}
                      {geo.coords.accuracyM > COARSE_ACCURACY_M &&
                        ' — poco precisa. Sé específico en la descripción (calle, cerro, referencia).'}
                    </p>
                    <button
                      type="button"
                      onClick={geo.request}
                      className="mt-1.5 text-xs font-semibold text-slate-600 underline underline-offset-2 hover:text-slate-900"
                    >
                      Actualizar ubicación
                    </button>
                  </>
                )}

                {(geo.status === 'error' || geo.status === 'unsupported') && (
                  <>
                    <p className="mt-1.5 text-sm leading-snug text-red-700">
                      {geo.error}
                    </p>
                    {!geo.denied && geo.status !== 'unsupported' && (
                      <button
                        type="button"
                        onClick={geo.request}
                        className="mt-2 rounded-lg bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-slate-700"
                      >
                        Reintentar
                      </button>
                    )}
                    <p className="mt-2 text-xs leading-snug text-slate-500">
                      Sin coordenadas no se puede enviar: un reporte sin lugar no
                      se puede correlacionar con ninguna otra señal.
                    </p>
                  </>
                )}
              </div>

              {/* --- Descripción ---------------------------------------------- */}
              <label
                htmlFor={textareaId}
                className="mt-4 block text-xs font-semibold uppercase tracking-wide text-slate-500"
              >
                Qué está pasando
              </label>
              <textarea
                id={textareaId}
                ref={textareaRef}
                value={text}
                onChange={(event) => setText(event.target.value.slice(0, REPORT_TEXT_MAX))}
                onBlur={() => setTouched(true)}
                rows={4}
                maxLength={REPORT_TEXT_MAX}
                disabled={isSubmitting}
                aria-invalid={showTextError}
                aria-describedby={`${textareaId}-help`}
                placeholder="Ej: columna de humo negro en el cerro sobre la Ruta 68, altura del peaje Zapata. Se ve desde la carretera."
                className={`
                  mt-1.5 w-full resize-y rounded-xl border px-3 py-2 text-sm text-slate-900
                  placeholder:text-slate-400 focus:outline-none focus:ring-2
                  disabled:bg-slate-50 disabled:text-slate-500
                  ${
                    showTextError
                      ? 'border-red-300 focus:border-red-400 focus:ring-red-200'
                      : 'border-slate-300 focus:border-slate-400 focus:ring-slate-300'
                  }
                `}
              />
              <div
                id={`${textareaId}-help`}
                className="mt-1 flex items-start justify-between gap-3 text-xs"
              >
                <span className={showTextError ? 'text-red-700' : 'text-slate-500'}>
                  {showTextError
                    ? `Describe la emergencia (mínimo ${REPORT_TEXT_MIN} caracteres).`
                    : 'Indica qué ves y una referencia del lugar.'}
                </span>
                <span className="shrink-0 tabular-nums text-slate-400">
                  {text.length}/{REPORT_TEXT_MAX}
                </span>
              </div>

              {errorMessage && (
                <p
                  role="alert"
                  className="mt-3 rounded-lg bg-red-50 p-2.5 text-xs leading-snug text-red-800 ring-1 ring-red-200"
                >
                  {errorMessage}
                </p>
              )}

              <p className="mt-4 rounded-lg bg-slate-50 p-2.5 text-[11px] leading-snug text-slate-500 ring-1 ring-slate-200">
                Esto no reemplaza a una llamada de emergencia. Si hay riesgo para
                una persona, llama al{' '}
                <strong className="text-slate-700">132</strong> (Bomberos) o al{' '}
                <strong className="text-slate-700">133</strong> (Carabineros).
              </p>
            </div>

            <footer className="flex gap-2 border-t border-slate-200 px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
              <button
                type="button"
                onClick={close}
                disabled={isSubmitting}
                className="flex-1 rounded-xl px-4 py-2.5 text-sm font-semibold text-slate-600 transition hover:bg-slate-100 disabled:opacity-40"
              >
                Cancelar
              </button>
              <button
                type="submit"
                disabled={!canSubmit}
                className="
                  flex flex-[2] items-center justify-center gap-2 rounded-xl bg-red-600 px-4 py-2.5
                  text-sm font-bold text-white shadow-sm transition
                  hover:bg-red-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400
                  focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300
                "
              >
                {isSubmitting && (
                  <span
                    aria-hidden
                    className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
                  />
                )}
                {isSubmitting
                  ? 'Enviando…'
                  : geo.status === 'locating'
                    ? 'Esperando ubicación…'
                    : 'Enviar reporte'}
              </button>
            </footer>
          </form>
        )}
      </div>
    </div>,
    document.body,
  )
}
