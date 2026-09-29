import { useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '@/api/client'
import type { HealthStatus } from '@/api/health'
import { fetchWaterCuts } from '@/api/waterCuts'
import type { WaterCut, WaterCutSource } from '@/api/waterCutTypes'
import { shouldWarn } from '@/components/ui/LayerHealth'
import { env } from '@/config/env'
import { pollEvery } from '@/lib/polling'
import { queryKeys } from '@/lib/queryClient'

/**
 * Estado de la capa de cortes de agua (Esval).
 *
 * # Siempre activa, como el radar
 *
 * La fila del panel lleva contador, y el contador tiene que estar ahí sin abrir
 * nada. Un mal día son ~30 cortes y unos pocos KB cada cinco minutos.
 *
 * # «Cuando lleguen datos»
 *
 * La fila no aparece hasta que el backend leyó a Esval al menos una vez
 * (`fuente.ultima_lectura`). Esval sólo responde a IP chilenas y el backend
 * sale por un proxy en Chile; mientras ese proxy no exista, no hay nada que
 * mostrar y una fila que diga «Sin datos» para siempre sería ruido. Hasta
 * entonces el estado es `unavailable`, pero —a diferencia del 404— se sigue
 * consultando: los datos llegan sin que nadie despliegue nada.
 *
 * # Los estados
 *
 *   - `loading`     — todavía no llegó nada.
 *   - `ready`       — hay cortes vigentes.
 *   - `empty`       — cero cortes y la fuente sana: no hay cortes.
 *   - `blind`       — cero cortes y la fuente NO está sana: no se sabe.
 *   - `error`       — la consulta falló y no hay nada en caché.
 *   - `unavailable` — el servidor no tiene la ruta (404, se deja de consultar)
 *                     o todavía no leyó a Esval nunca (se sigue consultando).
 */

type WaterCutsStatus = 'loading' | 'ready' | 'empty' | 'blind' | 'error' | 'unavailable'

export interface WaterCutsState {
  status: WaterCutsStatus
  /** Todos los vigentes, con y sin coordenadas. Nunca `undefined`. */
  cuts: WaterCut[]
  source: WaterCutSource | null
  sourceStatus: HealthStatus | undefined
  /** ¿Se muestra la fila? Falso con 404 y mientras el backend no haya leído a Esval. */
  available: boolean
  refetch: () => void
}

const NO_CUTS: WaterCut[] = []
const pollCuts = pollEvery(env.waterCutPollIntervalMs)

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

/** Emergencias primero; dentro de cada grupo, el que empezó más recién arriba. */
function byUrgency(a: WaterCut, b: WaterCut): number {
  const emergencyA = a.programado === false ? 0 : 1
  const emergencyB = b.programado === false ? 0 : 1
  if (emergencyA !== emergencyB) return emergencyA - emergencyB
  return (b.inicio ?? '').localeCompare(a.inicio ?? '')
}

export function useWaterCuts(enabled = env.waterCutsEnabled): WaterCutsState {
  const query = useQuery({
    queryKey: queryKeys.waterCuts.geojson(),
    queryFn: ({ signal }) => fetchWaterCuts(signal),
    enabled,
    staleTime: env.waterCutPollIntervalMs / 2,
    // Un 404 apaga el sondeo: la ruta aparece con un despliegue, no sola.
    refetchInterval: (q) => (isNotFound(q.state.error) ? false : pollCuts(q)),
  })

  const data = query.data
  const cuts = useMemo(
    () => (data ? [...data.cuts].sort(byUrgency) : NO_CUTS),
    [data],
  )
  const source = data?.fuente ?? null
  const sourceStatus = source?.estado
  const neverRead = data !== undefined && source?.ultima_lectura === null

  const status: WaterCutsStatus =
    !enabled || isNotFound(query.error) || neverRead
      ? 'unavailable'
      : data === undefined
        ? query.isError
          ? 'error'
          : 'loading'
        : cuts.length > 0
          ? 'ready'
          : shouldWarn(sourceStatus, 0)
            ? 'blind'
            : 'empty'

  const refetchQuery = query.refetch
  const refetch = useCallback(() => {
    void refetchQuery()
  }, [refetchQuery])

  // `loading` y `error` tampoco muestran la fila: sin una primera respuesta no
  // se sabe si el backend ya leyó a Esval alguna vez.
  const available = status === 'ready' || status === 'empty' || status === 'blind'

  // Memorizado: baja a `App` y de ahí a componentes detrás de `memo`.
  return useMemo(
    () => ({ status, cuts, source, sourceStatus, available, refetch }),
    [status, cuts, source, sourceStatus, available, refetch],
  )
}
