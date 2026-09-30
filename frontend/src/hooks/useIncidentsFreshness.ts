import { useCallback, useSyncExternalStore } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/queryClient'

const ACTIVE = [...queryKeys.incidents.all, 'active'] as const

interface IncidentsFreshness {
  /** Última respuesta exitosa de `/incidents/active`, en ms; 0 si ninguna. */
  updatedAt: number
  fetching: boolean
}

/**
 * Edad y actividad de la consulta de incidentes, leídas del caché.
 *
 * Lo usa el «en vivo» de la barra. No recibe `dataUpdatedAt` por props a
 * propósito: cambia en cada sondeo, y pasarlo desde `App` repintaría la barra
 * una vez por minuto (ver `App.renders.test.tsx`). Suscrito al caché, sólo se
 * repinta este indicador.
 */
export function useIncidentsFreshness(): IncidentsFreshness {
  const client = useQueryClient()
  const subscribe = useCallback(
    (listener: () => void) => client.getQueryCache().subscribe(listener),
    [client],
  )
  const read = useCallback(() => {
    let updatedAt = 0
    let fetching = false
    for (const query of client.getQueryCache().findAll({ queryKey: ACTIVE })) {
      updatedAt = Math.max(updatedAt, query.state.dataUpdatedAt)
      fetching ||= query.state.fetchStatus === 'fetching'
    }
    // Un número por campo: el snapshot tiene que ser estable entre lecturas.
    return `${updatedAt}|${fetching ? 1 : 0}`
  }, [client])
  const snapshot = useSyncExternalStore(subscribe, read, read)
  const [at, busy] = snapshot.split('|')
  return { updatedAt: Number(at), fetching: busy === '1' }
}
