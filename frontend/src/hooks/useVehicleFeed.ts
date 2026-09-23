import { useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '@/api/client'
import type { HealthStatus } from '@/api/health'
import { fetchVehicleFeed } from '@/api/vehicleFeed'
import type { VehicleFeedItem, VehicleFeedQuery, VehicleFeedSource } from '@/api/vehicleFeedTypes'
import { shouldWarn } from '@/components/ui/LayerHealth'
import { env } from '@/config/env'
import { FEED_LIMIT, FEED_WINDOW_HOURS, isRecent, visibleItems } from '@/domain/vehicleFeed'
import { queryKeys } from '@/lib/queryClient'
import { useNow } from './useNow'

/**
 * Estado del radar de vehículos.
 *
 * # Siempre activo, no diferido
 *
 * Al revés que las capas de referencia (`useRoadClosures`, `useRainLayer`), esta
 * consulta corre desde el arranque: el contador del botón tiene que estar ahí
 * sin que nadie abra nada. Es una respuesta chica (≤ 100 avisos sin geometría)
 * cada cinco minutos, y react-query la pausa con la pestaña en segundo plano.
 *
 * # Los seis estados, y por qué `empty` y `blind` son distintos
 *
 *   - `loading`     — todavía no llegó nada.
 *   - `ready`       — hay avisos en la ventana.
 *   - `empty`       — cero avisos Y la fuente está sana. Es una respuesta
 *                     correcta: no hubo publicaciones nuevas.
 *   - `blind`       — cero avisos pero la fuente NO está sana. Decir «sin
 *                     avisos» acá sería afirmar calma sin haber mirado. Misma
 *                     regla que `shouldWarn` en las capas del mapa.
 *   - `error`       — la consulta falló y no hay nada en caché.
 *   - `unavailable` — el servidor no tiene la ruta (404). Pasa mientras el
 *                     backend desplegado sea anterior al feed. No se reintenta
 *                     ni se sigue consultando: no es un corte, es una versión.
 */

export type VehicleFeedStatus = 'loading' | 'ready' | 'empty' | 'blind' | 'error' | 'unavailable'

export interface VehicleFeedState {
  status: VehicleFeedStatus
  /** Dentro de la ventana de 48 h, el más nuevo arriba. Nunca `undefined`. */
  items: VehicleFeedItem[]
  count: number
  /** Cuántos tienen menos de 6 h. */
  recentCount: number
  source: VehicleFeedSource | null
  sourceStatus: HealthStatus | undefined
  /** Reloj con que se filtró. Los componentes lo usan para «hace X». */
  now: number
  /** Hay datos, pero el último intento de refrescarlos falló. */
  refreshFailed: boolean
  /** Llegaron tantos avisos como el tope: puede haber más. */
  truncated: boolean
  isFetching: boolean
  refetch: () => void
}

const FEED_PARAMS: VehicleFeedQuery = { horas: FEED_WINDOW_HOURS, limit: FEED_LIMIT }

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

const NO_ITEMS: VehicleFeedItem[] = []

export function useVehicleFeed(enabled = env.vehicleRadarEnabled): VehicleFeedState {
  const now = useNow(60_000)

  const query = useQuery({
    queryKey: queryKeys.vehicles.feed(FEED_PARAMS),
    queryFn: ({ signal }) => fetchVehicleFeed(FEED_PARAMS, signal),
    enabled,
    staleTime: env.vehiclePollIntervalMs / 2,
    // Un 404 apaga el sondeo: la ruta no va a aparecer sola dentro de cinco
    // minutos, aparece con un despliegue, y ahí se recarga la app.
    refetchInterval: (q) => (isNotFound(q.state.error) ? false : env.vehiclePollIntervalMs),
  })

  const data = query.data
  const items = useMemo(() => (data ? visibleItems(data.items, now) : NO_ITEMS), [data, now])
  const recentCount = useMemo(() => items.filter((item) => isRecent(item, now)).length, [items, now])

  const source = data?.fuente ?? null
  const sourceStatus = source?.estado

  const status: VehicleFeedStatus = isNotFound(query.error)
    ? 'unavailable'
    : data === undefined
      ? query.isError
        ? 'error'
        : 'loading'
      : items.length > 0
        ? 'ready'
        : shouldWarn(sourceStatus, 0)
          ? 'blind'
          : 'empty'

  const refetchQuery = query.refetch
  const refetch = useCallback(() => {
    void refetchQuery()
  }, [refetchQuery])

  return {
    status,
    items,
    count: items.length,
    recentCount,
    source,
    sourceStatus,
    now,
    refreshFailed: data !== undefined && query.isError,
    truncated: (data?.items.length ?? 0) >= FEED_LIMIT,
    isFetching: query.isFetching,
    refetch,
  }
}
