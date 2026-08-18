import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { fetchActiveIncidents } from '@/api/incidents'
import type { ActiveIncidentsQuery } from '@/api/types'
import { env } from '@/config/env'
import { queryKeys } from '@/lib/queryClient'

/**
 * Polling de `/incidents/active`.
 *
 * `keepPreviousData` evita que el mapa parpadee en vacío cada vez que cambia un
 * filtro: se siguen mostrando los marcadores anteriores mientras llega la
 * respuesta nueva.
 */
export function useActiveIncidents(params: ActiveIncidentsQuery = {}) {
  return useQuery({
    queryKey: queryKeys.incidents.active(params),
    queryFn: ({ signal }) => fetchActiveIncidents(params, signal),
    refetchInterval: env.pollIntervalMs,
    placeholderData: keepPreviousData,
  })
}
