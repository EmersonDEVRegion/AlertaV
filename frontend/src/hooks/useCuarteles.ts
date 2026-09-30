import { useCallback, useMemo, useSyncExternalStore } from 'react'
import { useQuery } from '@tanstack/react-query'
import { EMPTY_CUARTELES, fetchCuarteles, type CuartelesData } from '@/api/cuarteles'
import { queryKeys } from '@/lib/queryClient'

/**
 * Estado de la capa «Cuarteles de Bomberos».
 *
 * Misma arquitectura que `useSeismicHazard` —`enabled` es intención del usuario
 * y el estado de carga se observa de la consulta—, con una diferencia: la
 * intención vive en un store de módulo y no en `useState`. Hay dos sitios que
 * la escriben: el interruptor de «Capas de referencia» y el «Ver en el mapa» de
 * «Mis lugares», que está en otra rama del árbol. Con un store, encender la
 * capa desde «Mis lugares» no obliga a subir el estado hasta `App`.
 *
 * `hasMounted` es monótono, como en las otras capas diferidas: el `<Source>`
 * entra al árbol la primera vez que alguien la enciende y ya no sale.
 */

export type CuartelesStatus = 'idle' | 'loading' | 'ready' | 'error'

interface Intencion {
  enabled: boolean
  hasMounted: boolean
}

let intencion: Intencion = Object.freeze({ enabled: false, hasMounted: false })
const listeners = new Set<() => void>()

function set(cambios: Partial<Intencion>): void {
  intencion = Object.freeze({ ...intencion, ...cambios })
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

const getSnapshot = (): Intencion => intencion

/** Enciende la capa (sin apagarla si ya estaba). Para «Ver en el mapa». */
export function mostrarCuarteles(): void {
  set({ enabled: true, hasMounted: true })
}

/**
 * Una instantánea estática: se pide una vez por sesión. El endpoint responde con
 * `ETag`, así que la próxima sesión cuesta un 304.
 */
const QUERY_OPTIONS = {
  staleTime: Number.POSITIVE_INFINITY,
  gcTime: Number.POSITIVE_INFINITY,
  refetchInterval: false,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
} as const

/**
 * Los datos, para quien los necesite aunque la capa esté apagada («Mis
 * lugares» calcula los más cercanos). `active` decide si se piden.
 */
export function useCuartelesData(active: boolean) {
  return useQuery({
    queryKey: queryKeys.cuarteles.all,
    queryFn: ({ signal }) => fetchCuarteles(signal),
    enabled: active,
    ...QUERY_OPTIONS,
  })
}

export interface CuartelesState {
  enabled: boolean
  hasMounted: boolean
  status: CuartelesStatus
  data: CuartelesData
  count: number
  toggle: () => void
  retry: () => void
  errorMessage: string | null
}

export function useCuarteles(): CuartelesState {
  const { enabled, hasMounted } = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  const query = useCuartelesData(hasMounted)

  const toggle = useCallback(() => {
    set({ enabled: !intencion.enabled, hasMounted: true })
  }, [])

  const refetch = query.refetch
  const retry = useCallback(() => {
    set({ enabled: true, hasMounted: true })
    void refetch()
  }, [refetch])

  const data = query.data ?? EMPTY_CUARTELES
  const status: CuartelesStatus = !hasMounted
    ? 'idle'
    : query.isError
      ? 'error'
      : query.data === undefined
        ? 'loading'
        : 'ready'
  const errorMessage =
    status === 'error' ? ((query.error as Error | null)?.message ?? null) : null

  return useMemo(
    () => ({
      enabled,
      hasMounted,
      status,
      data,
      count: data.cuarteles.length,
      toggle,
      retry,
      errorMessage,
    }),
    [enabled, hasMounted, status, data, toggle, retry, errorMessage],
  )
}
