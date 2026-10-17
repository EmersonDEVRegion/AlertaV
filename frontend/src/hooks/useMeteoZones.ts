import { useQuery } from '@tanstack/react-query'
import { isNotFound } from '@/api/client'
import { EMPTY_METEO_ZONES, fetchMeteoZones, type MeteoZoneCollection } from '@/api/meteoZones'
import { env } from '@/config/env'
import { pollEvery } from '@/lib/polling'
import { queryKeys } from '@/lib/queryClient'
import { useMeteoZonesState, type MeteoZonesState } from '@/lib/meteoZonesStore'

/**
 * La capa «Avisos DMC»: su estado (del store) y sus zonas (de la API).
 *
 * La consulta sólo corre con la capa encendida: hasta el primer gesto no se
 * pide nada. Mismos estados que las otras capas de referencia; `empty` es «la
 * DMC no tiene avisos para la región», que no es una falla.
 */
export type MeteoZonesStatus = 'idle' | 'loading' | 'ready' | 'empty' | 'error' | 'unavailable'

export interface MeteoZonesLayerState extends MeteoZonesState {
  data: MeteoZoneCollection
  status: MeteoZonesStatus
}

const poll = pollEvery(env.newsPollIntervalMs)

export function useMeteoZones(): MeteoZonesLayerState {
  const state = useMeteoZonesState()
  const query = useQuery({
    queryKey: queryKeys.meteoWarnings.zones,
    queryFn: ({ signal }) => fetchMeteoZones(signal),
    enabled: state.enabled,
    staleTime: env.newsPollIntervalMs / 2,
    refetchInterval: (q) => (isNotFound(q.state.error) ? false : poll(q)),
  })

  const data = query.data ?? EMPTY_METEO_ZONES
  const status: MeteoZonesStatus = !state.hasMounted
    ? 'idle'
    : isNotFound(query.error)
      ? 'unavailable'
      : query.isError && query.data === undefined
        ? 'error'
        : query.data === undefined
          ? 'loading'
          : data.features.length === 0
            ? 'empty'
            : 'ready'

  return { ...state, data, status }
}
