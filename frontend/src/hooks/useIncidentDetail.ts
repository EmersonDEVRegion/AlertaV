import { useQuery } from '@tanstack/react-query'
import { fetchIncidentDetail } from '@/api/incidents'
import { env } from '@/config/env'
import { pollEvery } from '@/lib/polling'
import { queryKeys } from '@/lib/queryClient'

const refetchInterval = pollEvery(env.pollIntervalMs)

/**
 * Detalle con la traza completa de señales. Solo se pide cuando hay una tarjeta
 * abierta: es una consulta más cara y no tiene sentido mantenerla viva mientras
 * el usuario mira el mapa.
 */
export function useIncidentDetail(code: string | null) {
  return useQuery({
    queryKey: queryKeys.incidents.detail(code ?? ''),
    queryFn: ({ signal }) => fetchIncidentDetail(code as string, signal),
    enabled: Boolean(code),
    refetchInterval,
  })
}
