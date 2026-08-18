import { useQuery } from '@tanstack/react-query'
import { fetchIncidentDetail } from '@/api/incidents'
import { env } from '@/config/env'
import { queryKeys } from '@/lib/queryClient'

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
    refetchInterval: env.pollIntervalMs,
  })
}
