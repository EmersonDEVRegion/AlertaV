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
      <p className="px-3 py-1 text-center text-[11px] text-slate-500 dark:text-slate-400">
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
      className="flex items-center justify-between gap-3 bg-amber-100 px-3 py-2 text-amber-950 ring-1 ring-amber-300 dark:bg-amber-950/60 dark:text-amber-100 dark:ring-amber-800/60"
    >
      <p className="text-xs leading-snug">
        <span className="font-semibold">{message}</span>{' '}
        {dataUpdatedAt ? (
          <span className="text-amber-800 dark:text-amber-300">
            Última actualización {formatRelative(dataUpdatedAt)}.
          </span>
        ) : (
          <span className="text-amber-800 dark:text-amber-300">Aún no se recibe ningún dato.</span>
        )}
      </p>
      <button
        type="button"
        onClick={onRetry}
        disabled={isFetching}
        className="shrink-0 rounded-full bg-amber-950 px-3 py-1 text-xs font-semibold text-amber-50 transition hover:bg-amber-900 disabled:opacity-50 dark:bg-amber-200 dark:text-amber-950 dark:hover:bg-amber-100"
      >
        {isFetching ? 'Buscando…' : 'Reintentar'}
      </button>
    </div>
  )
}
