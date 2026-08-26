import type { Freshness } from '@/hooks/useFreshness'
import { formatRelative } from '@/lib/format'

interface StalenessBannerProps {
  freshness: Freshness
  isOnline: boolean
  isFetching: boolean
  dataUpdatedAt: number | undefined
  hasError: boolean
  onRetry: () => void
}

/**
 * Antiguedad del dato en pantalla.
 *
 * Es la contraparte obligatoria de haber decidido cachear incidentes en el
 * service worker. La cache permite que la app abra sin señal; este cartel
 * impide que lo que muestra se lea como si fuera de ahora. Sin el, la decisión
 * de cachear seria una forma de desinformar.
 */
export function StalenessBanner({
  freshness,
  isOnline,
  isFetching,
  dataUpdatedAt,
  hasError,
  onRetry,
}: StalenessBannerProps) {
  const showWarning = !isOnline || freshness.isStale || hasError
  if (!showWarning && !isFetching) return null

  if (!showWarning) {
    return (
      <p className="px-3 py-1 text-center text-[11px] text-ink-muted">
        Actualizando…
      </p>
    )
  }

  const message = !isOnline
    ? 'Sin conexión. Estos datos pueden no reflejar la situacion actual.'
    : hasError
      ? 'No se pudo contactar al servidor. Mostrando el último dato recibido.'
      : 'Los datos podrían estar desactualizados.'

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center justify-between gap-3 bg-warn-bg px-3 py-2 text-warn-ink ring-1 ring-warn-line"
    >
      <p className="text-xs leading-snug">
        <span className="font-semibold">{message}</span>{' '}
        {dataUpdatedAt ? (
          <span className="text-warn-ink">
            Última actualización {formatRelative(dataUpdatedAt)}.
          </span>
        ) : (
          <span className="text-warn-ink">Aún no se recibe ningún dato.</span>
        )}
      </p>
      <button
        type="button"
        onClick={onRetry}
        disabled={isFetching}
        className="shrink-0 rounded-full bg-warn-ink px-3 py-1 text-xs font-semibold text-warn-bg transition hover:opacity-90 disabled:opacity-50"
      >
        {isFetching ? 'Buscando…' : 'Reintentar'}
      </button>
    </div>
  )
}
