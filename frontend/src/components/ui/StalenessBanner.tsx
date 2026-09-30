import { memo } from 'react'
import { useIsFetching } from '@tanstack/react-query'
import { useFreshness } from '@/hooks/useFreshness'
import { useOnlineStatus } from '@/hooks/useOnlineStatus'
import { formatRelative } from '@/lib/format'
import { queryKeys } from '@/lib/queryClient'

interface StalenessBannerProps {
  dataUpdatedAt: number | undefined
  hasError: boolean
  onRetry: () => void
}

/** Prefijo de la consulta de incidentes activos, con cualquier filtro. */
const ACTIVE_INCIDENTS_KEY = [...queryKeys.incidents.all, 'active'] as const

/**
 * Antiguedad del dato en pantalla.
 *
 * Es la contraparte obligatoria de haber decidido cachear incidentes en el
 * service worker. La cache permite que la app abra sin señal; este cartel
 * impide que lo que muestra se lea como si fuera de ahora. Sin el, la decisión
 * de cachear seria una forma de desinformar.
 */
export const StalenessBanner = memo(function StalenessBanner({
  dataUpdatedAt,
  hasError,
  onRetry,
}: StalenessBannerProps) {
  /*
   * Los tres relojes de este cartel viven ACÁ y no en `App`.
   *
   * La edad del dato cambia cada pocos segundos, la conexión cuando quiere y
   * «actualizando» dos veces por sondeo. Si `App` los leyera, cada uno de esos
   * cambios repintaría el mapa, los dos paneles y la barra: medido, eran 60
   * renders por minuto del mapa con la aplicación quieta.
   */
  const freshness = useFreshness(dataUpdatedAt)
  const isOnline = useOnlineStatus()
  const isFetching = useIsFetching({ queryKey: ACTIVE_INCIDENTS_KEY }) > 0

  const showWarning = !isOnline || freshness.isStale || hasError
  // «Actualizando…» ya no flota sobre el mapa: lo dice la barra, bajo la
  // marca (`LiveStatus`). Este cartel queda sólo para cuando hay un problema.
  if (!showWarning) return null

  const message = !isOnline
    ? 'Sin conexión. Estos datos pueden no reflejar la situación actual.'
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
})
