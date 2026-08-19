import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { fetchSeismicEvents } from '@/api/seismic'
import type { SeismicQuery } from '@/api/seismicTypes'
import { env } from '@/config/env'
import { queryKeys } from '@/lib/queryClient'

/**
 * Polling de `/events/seismic`.
 *
 * Cadencia propia, más lenta que la de incidentes: el collector del USGS corre
 * cada 5 minutos (`USGS_POLL_INTERVAL_SECONDS`) y un sismo, a diferencia de un
 * incendio, no cambia de estado mientras ocurre. Lo único que se corrige es la
 * magnitud cuando la solución pasa de `automatic` a `reviewed`, y eso tarda.
 *
 * `enabled` permite apagar la consulta cuando la capa está oculta: no tiene
 * sentido gastar red y batería trayendo sismos que nadie está mirando.
 */
export function useSeismicEvents(params: SeismicQuery = {}, enabled = true) {
  return useQuery({
    queryKey: queryKeys.seismic.list(params),
    queryFn: ({ signal }) => fetchSeismicEvents(params, signal),
    refetchInterval: env.seismicPollIntervalMs,
    placeholderData: keepPreviousData,
    enabled,
  })
}
