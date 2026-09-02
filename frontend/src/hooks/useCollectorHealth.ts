import { useQuery } from '@tanstack/react-query'
import { fetchCollectorsHealth } from '@/api/health'
import { env } from '@/config/env'

/**
 * Salud de la recolección.
 *
 * Se consulta más lento que los incidentes —esto cambia en la escala de las
 * cadencias de los collectors, no en la de un choque— pero se consulta
 * SIEMPRE, no sólo cuando hay algo abierto: su valor aparece justamente cuando
 * el mapa está vacío, que es cuando nadie está tocando nada.
 *
 * Si la consulta falla, `data` queda indefinido y la interfaz vuelve a mostrar
 * los contadores tal cual. Es la degradación correcta: no saber si una capa ve
 * no autoriza a afirmar que está ciega.
 */
export function useCollectorHealth() {
  return useQuery({
    queryKey: ['collectors', 'health'],
    queryFn: ({ signal }) => fetchCollectorsHealth(signal),
    refetchInterval: env.pollIntervalMs * 2,
  })
}
