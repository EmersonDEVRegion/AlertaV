import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { REPORT_TEXT_MAX, REPORT_TEXT_MIN } from '@/api/citizenReports'
import {
  REPORT_CATEGORIES,
  REPORT_CATEGORY_INFO,
  emergencyCallHint,
  successCallHint,
  type ReportCategory,
} from '@/domain/reportCategories'
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
  /**
   * Sin preselección a propósito. Un valor por defecto —"Incendio", que era el
   * supuesto implícito de toda la aplicación— haría que una persona apurada
   * enviara un choque tipificado como incendio sin darse cuenta, y el motor lo
   * correlacionaría con la familia equivocada. Que la categoría sea una
   * decisión consciente cuesta un toque y ahorra un dato falso.
   */
  const [category, setCategory] = useState<ReportCategory | null>(null)

  const geo = useGeolocation()
  const { submit, isSubmitting, isSuccess, errorMessage, reset } = useCitizenReport()

  const titleId = useId()
  const categoryGroupId = useId()
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
  const showCategoryError = touched && category === null
  const canSubmit =
    geo.status === 'ready' &&
    !!geo.coords &&
    !textTooShort &&
    category !== null &&
    !isSubmitting

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setTouched(true)
    if (!geo.coords || textTooShort || category === null || isSubmitting) return

    try {
      await submit({
        lat: geo.coords.lat,
        lon: geo.coords.lon,
        category,
        text: trimmed,
      })
    } catch {
      // `useCitizenReport` ya tradujo el error a `errorMessage`. Este catch solo
      // existe para que el rechazo de la promesa no escale a un unhandled.
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-scrim p-0 backdrop-blur-sm sm:items-center sm:p-4"
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
        className="flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-2xl bg-raised
          shadow-2xl ring-1 ring-line
          sm:max-w-md sm:rounded-surface
 "
      >
        <header className="flex items-start gap-3 border-b border-line px-5 py-4">
          <span
            aria-hidden
            className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-full bg-danger-bg text-lg"
          >
            🚨
          </span>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-base font-bold text-ink">
              Reportar emergencia
            </h2>
            <p className="mt-0.5 text-xs leading-snug text-ink-muted">
              Tu reporte entra como una señal más al motor de correlación.
            </p>
          </div>
          <button
            type="button"
            onClick={close}
            disabled={isSubmitting}
            aria-label="Cerrar formulario de reporte"
            className="-mr-1 grid size-9 shrink-0 place-items-center rounded-full text-ink-faint transition hover:bg-sunken hover:text-ink-muted disabled:opacity-40"
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
              className="grid size-12 place-items-center rounded-full bg-success-bg text-2xl text-success-ink"
            >
              ✓
            </span>
            <p className="text-base font-bold text-ink">Reporte enviado</p>
            <p className="max-w-xs text-xs leading-snug text-ink-muted">
              Quedó registrado como señal ciudadana. Si otras fuentes lo
              corroboran, aparecerá en el mapa como incidente en los próximos
              minutos.
            </p>
            <p className="mt-2 text-xs font-semibold text-danger-ink">
              {category ? successCallHint(category) : null}
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
            <div className="flex-1 overflow-y-auto px-5 py-4">
              {/* --- Ubicación ------------------------------------------------ */}
              <div className="rounded-surface bg-sunken p-3 ring-1 ring-line">
                <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
                  Tu ubicación
                </p>

                {geo.status === 'locating' && (
                  <p className="mt-1.5 flex items-center gap-2 text-sm text-ink-muted">
                    <span
                      aria-hidden
                      className="size-3.5 animate-spin rounded-full border-2 border-line-strong border-t-ink-muted"
                    />
                    Obteniendo ubicación…
                  </p>
                )}

                {geo.status === 'ready' && geo.coords && (
                  <>
                    <p className="mt-1.5 font-mono text-sm text-ink">
                      {geo.coords.lat.toFixed(5)}, {geo.coords.lon.toFixed(5)}
                    </p>
                    <p className="mt-0.5 text-xs text-ink-muted">
                      Precisión {formatAccuracy(geo.coords.accuracyM)}
                      {geo.coords.accuracyM > COARSE_ACCURACY_M &&
                        ' — poco precisa. Sé específico en la descripción (calle, cerro, referencia).'}
                    </p>
                    <button
                      type="button"
                      onClick={geo.request}
                      className="mt-1.5 text-xs font-semibold text-ink-muted underline underline-offset-2 hover:text-ink"
                    >
                      Actualizar ubicación
                    </button>
                  </>
                )}

                {(geo.status === 'error' || geo.status === 'unsupported') && (
                  <>
                    <p className="mt-1.5 text-sm leading-snug text-danger-ink">
                      {geo.error}
                    </p>
                    {!geo.denied && geo.status !== 'unsupported' && (
                      <button
                        type="button"
                        onClick={geo.request}
                        className="mt-2 rounded-control bg-chrome-raised px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-chrome-raised"
                      >
                        Reintentar
                      </button>
                    )}
                    <p className="mt-2 text-xs leading-snug text-ink-muted">
                      Sin coordenadas no se puede enviar: un reporte sin lugar no
                      se puede correlacionar con ninguna otra señal.
                    </p>
                  </>
                )}
              </div>

              {/* --- Categoría ------------------------------------------------ */}
              {/*
                `fieldset`/`legend` y no un `div` con texto: es lo que hace que
                un lector de pantalla anuncie "Qué tipo de emergencia, grupo de
                3 opciones" en vez de leer tres radios sueltos sin contexto.
              */}
              <fieldset className="mt-4" aria-describedby={`${categoryGroupId}-error`}>
                <legend className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
                  Qué tipo de emergencia
                </legend>

                <div className="mt-1.5 grid gap-2">
                  {REPORT_CATEGORIES.map((value) => {
                    const info = REPORT_CATEGORY_INFO[value]
                    const selected = category === value
                    return (
                      <label
                        key={value}
                        className={`
                          flex cursor-pointer items-start gap-3 rounded-surface border p-3 transition
                          focus-within:ring-2 focus-within:ring-red-300
                          ${
                            selected
                              ? 'border-danger-line bg-danger-bg ring-1 ring-danger-line'
                              : 'border-line-strong bg-raised hover:bg-sunken'
                          }
                        `}
                      >
                        <input
                          type="radio"
                          name="report-category"
                          value={value}
                          checked={selected}
                          onChange={() => setCategory(value)}
                          disabled={isSubmitting}
                          className="mt-0.5 size-4 shrink-0 accent-red-600"
                        />
                        <span aria-hidden className="text-lg leading-none">
                          {info.emoji}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-semibold text-ink">
                            {info.label}
                          </span>
                          <span className="block text-xs leading-snug text-ink-muted">
                            {info.hint}
                          </span>
                        </span>
                      </label>
                    )
                  })}
                </div>

                {showCategoryError && (
                  <p
                    id={`${categoryGroupId}-error`}
                    className="mt-1.5 text-xs text-danger-ink"
                  >
                    Elige el tipo de emergencia. El sistema lo usa para
                    correlacionar tu reporte con las fuentes correctas.
                  </p>
                )}
              </fieldset>

              {/* --- Descripción ---------------------------------------------- */}
              <label
                htmlFor={textareaId}
                className="mt-4 block text-xs font-semibold uppercase tracking-wide text-ink-muted"
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
                placeholder={
                  category
                    ? REPORT_CATEGORY_INFO[category].placeholder
                    : 'Describe qué ves y una referencia del lugar.'
                }
                className={`
                  mt-1.5 w-full resize-y rounded-surface border px-3 py-2 text-sm text-ink
                  placeholder:text-ink-faint focus:outline-none focus:ring-2
                  disabled:bg-sunken disabled:text-ink-muted
                  ${
                    showTextError
                      ? 'border-danger-line focus:border-danger-ink focus:ring-danger-line'
                      : 'border-line-strong focus:border-line-strong focus:ring-line'
                  }
                `}
              />
              <div
                id={`${textareaId}-help`}
                className="mt-1 flex items-start justify-between gap-3 text-xs"
              >
                <span
                  className={
                    showTextError
                      ? 'text-danger-ink'
                      : 'text-ink-muted'
                  }
                >
                  {showTextError
                    ? `Describe la emergencia (mínimo ${REPORT_TEXT_MIN} caracteres).`
                    : 'Indica qué ves y una referencia del lugar.'}
                </span>
                <span className="shrink-0 tabular-nums text-ink-faint">
                  {text.length}/{REPORT_TEXT_MAX}
                </span>
              </div>

              {errorMessage && (
                <p
                  role="alert"
                  className="mt-3 callout callout-danger text-xs"
                >
                  {errorMessage}
                </p>
              )}

              {/*
                Texto dinámico. Antes decía siempre "llama al 132" porque la
                aplicación nació siendo un mapa de incendios; mandar a llamar a
                Bomberos por un choque sin fuego retrasa la respuesta correcta.
                La tabla de números vive en `domain/reportCategories.ts`.
              */}
              <p className="mt-4 rounded-control bg-sunken p-2.5 text-[11px] leading-snug text-ink-muted ring-1 ring-line">
                {emergencyCallHint(category)}
              </p>
            </div>

            <footer className="flex gap-2 border-t border-line px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
              <button
                type="button"
                onClick={close}
                disabled={isSubmitting}
                className="flex-1 rounded-surface px-4 py-2.5 text-sm font-semibold text-ink-muted transition hover:bg-sunken hover:text-ink disabled:opacity-40"
              >
                Cancelar
              </button>
              <button
                type="submit"
                disabled={!canSubmit}
                className="flex flex-[2] items-center justify-center gap-2 rounded-surface bg-red-600 px-4 py-2.5
                  text-sm font-bold text-white shadow-sm transition
                  hover:bg-red-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400
                  focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-line-strong
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
                    : category === null
                      ? 'Elige el tipo'
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
