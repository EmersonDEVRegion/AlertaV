import { useCallback, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  EMPTY_ROAD_CLOSURES,
  countCutRoutes,
  fetchRoadClosuresGeojson,
} from '@/api/roadClosures'
import type { RoadClosureCollection, RoadClosureQuery } from '@/api/roadClosureTypes'
import { env } from '@/config/env'
import { queryKeys } from '@/lib/queryClient'

/**
 * Estado de la capa de cortes de ruta.
 *
 * # Carga diferida: dos banderas y una consulta apagada
 *
 * Mismo patrón que `useRainLayer` y `useSeismicHazard`, y por los mismos
 * motivos:
 *
 *   - `enabled`    — lo que el usuario pide ahora.
 *   - `hasMounted` — si la fuente llegó a existir alguna vez. Monótona.
 *
 * La llamada **no ocurre hasta el primer encendido**, y eso no lo consigue un
 * `if` en el componente sino `enabled` de react-query: mientras esté en `false`
 * la consulta no se dispara, no reintenta y no aparece en la red. El arranque
 * de la aplicación no paga nada por esta capa.
 *
 * `hasMounted` hace el trabajo complementario en el mapa: una vez montado, el
 * `<Source>` se queda para siempre y apagar la capa sólo cambia `visibility`.
 * El error clásico es atar el montaje a `enabled`: funciona, pero cada apagado
 * destruye la fuente y cada encendido vuelve a subir el GeoJSON al worker.
 *
 * # Cadencia: la más lenta de todas, y con mucho
 *
 * El collector del MOP corre **cada hora** y el propio servicio declara que se
 * actualiza los lunes ~15:00, a diario sólo durante emergencias. El del MTT
 * corre cada 10 minutos, pero sus avisos son intervenciones programadas que no
 * cambian de un momento a otro.
 *
 * Refrescar esto cada minuto traería la misma foto sesenta veces. Quince
 * minutos ya es generoso, y lo relevante no es la latencia sino que el dato
 * esté cuando alguien encienda la capa.
 *
 * # Por qué el estado vacío se llama `empty` y no `error`
 *
 * Ninguna ruta cortada en la región es una respuesta **correcta** y frecuente.
 * El panel tiene que poder decir «sin cortes informados» y nunca «sin datos»:
 * son afirmaciones distintas y sólo una de las dos es verdad.
 */

export type RoadClosureStatus = 'idle' | 'loading' | 'ready' | 'empty' | 'error'

export interface RoadClosureState {
  enabled: boolean
  /** ¿Debe existir el `<Source>` en el árbol? Monótono. */
  hasMounted: boolean
  status: RoadClosureStatus
  /** Nunca `undefined`: «ninguna ruta cortada» es una colección vacía, no un hueco. */
  data: RoadClosureCollection
  /** Cortes e intervenciones vigentes, de las dos fuentes. */
  count: number
  /** Cuántos son intransitables (`severidad >= 4`). */
  cutCount: number
  /** Atajo para no arrancar la animación cuando no hay nada que pulsar. */
  hasCut: boolean
  toggle: () => void
  retry: () => void
}

/**
 * Sin parámetros: los defectos del backend ya son los correctos para el mapa
 * —`hours=720` cubre la cadencia semanal del MOP y `limit=500` es una red de
 * seguridad que nunca se toca—. Dejarlo vacío mantiene la URL estable y por
 * tanto también la clave de caché.
 */
const CLOSURE_PARAMS: RoadClosureQuery = {}

export function useRoadClosures(): RoadClosureState {
  const [enabled, setEnabled] = useState(false)
  const [hasMounted, setHasMounted] = useState(false)

  const query = useQuery({
    queryKey: queryKeys.roadClosures.geojson(CLOSURE_PARAMS),
    queryFn: ({ signal }) => fetchRoadClosuresGeojson(CLOSURE_PARAMS, signal),
    // La línea de la carga diferida. Sin esto, la capa costaría una llamada en
    // cada arranque aunque nadie la encienda nunca.
    enabled,
    staleTime: env.roadClosurePollIntervalMs,
    refetchInterval: env.roadClosurePollIntervalMs,
  })

  const toggle = useCallback(() => {
    setEnabled((current) => {
      if (!current) setHasMounted(true)
      return !current
    })
  }, [])

  const refetch = query.refetch
  const retry = useCallback(() => {
    setEnabled(true)
    setHasMounted(true)
    void refetch()
  }, [refetch])

  const data = query.data ?? EMPTY_ROAD_CLOSURES
  const cutCount = useMemo(() => countCutRoutes(data), [data])

  const status: RoadClosureStatus = !hasMounted
    ? 'idle'
    : query.isError && query.data === undefined
      ? 'error'
      : query.data === undefined
        ? 'loading'
        : data.features.length === 0
          ? 'empty'
          : 'ready'

  return useMemo(
    () => ({
      enabled,
      hasMounted,
      status,
      data,
      count: data.features.length,
      cutCount,
      hasCut: cutCount > 0,
      toggle,
      retry,
    }),
    [enabled, hasMounted, status, data, cutCount, toggle, retry],
  )
}
