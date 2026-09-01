import { useCallback, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { EMPTY_HAZARD, fetchHazardGrid } from '@/api/hazard'
import type { HazardGrid } from '@/api/hazardTypes'
import { queryKeys } from '@/lib/queryClient'

/**
 * Estado de la capa de amenaza sísmica.
 *
 * ===========================================================================
 * EL BUG QUE ESTE ARCHIVO EXISTE PARA NO REPETIR
 * ===========================================================================
 *
 * Síntoma: se encendía el interruptor, la capa cargaba y, justo al terminar, el
 * pulgar «rebotaba» a la izquierda. Un segundo clic mostraba la capa al
 * instante — porque para entonces ya estaba descargada.
 *
 * La causa NO era un desajuste de renderizado de React. Era un cruce de dos
 * decisiones que por separado parecían razonables:
 *
 *   1. **`onError` apagaba el interruptor.** El razonamiento original está
 *      documentado y es defendible: «un control encendido tiene que
 *      corresponder a algo dibujado». Pero convierte el resultado de una
 *      descarga en una escritura sobre la INTENCIÓN del usuario, y una
 *      intención que el sistema puede reescribir sola deja de ser un control.
 *
 *   2. **Un cronómetro de 15 s declaraba el fallo a ciegas.** Existía para
 *      cubrir el modo de falla más molesto —el silencio: MapLibre no emite
 *      `error` ni `sourcedata` si la respuesta nunca llega—, pero medía tiempo
 *      de pared, no progreso. Con la grilla del CSN sobre una red lenta, los
 *      15 s se cumplían **con la descarga aún en curso**. El cronómetro
 *      llamaba a `onError`, `onError` apagaba `enabled`, y unos segundos
 *      después llegaba `sourcedata` y ponía `status: 'ready'`. Resultado
 *      exacto del reporte: el interruptor cae justo cuando la capa termina de
 *      cargar, el aviso de error ni siquiera alcanza a verse, y la capa está
 *      lista esperando un segundo clic.
 *
 * A eso se sumaba una tercera fragilidad en `SeismicHazardLayer`: `isSourceLoaded`
 * se consultaba una sola vez al montar y el resto se dejaba a los eventos del
 * mapa. Con el archivo en la caché del navegador la fuente podía quedar cargada
 * ANTES de que `on('sourcedata')` se enganchara, y entonces el evento no se
 * perdía: nunca se emitía. Había un `try/catch` que cerraba esa ventana, pero
 * cerrar una ventana no es lo mismo que no tener carrera.
 *
 * ---------------------------------------------------------------------------
 * LA CORRECCIÓN: dos reglas, y la segunda hace innecesaria la máquina entera
 * ---------------------------------------------------------------------------
 *
 * **Regla 1 — `enabled` es intención del usuario y sólo la escribe el usuario.**
 * Ninguna ruta de fallo la toca. Si la descarga falla, el interruptor se queda
 * donde lo dejaron y la fila lo explica con su subtítulo y un botón de
 * reintentar. Es la diferencia entre «apagué la capa por ti» y «no pude
 * dibujarla, mira por qué».
 *
 * **Regla 2 — el estado de carga se OBSERVA de la promesa, no de los eventos
 * del mapa.** Con `useQuery` no hay carrera posible: no existe el instante
 * entre «la fuente ya cargó» y «todavía no escucho», porque no hay escucha. Y
 * el cronómetro a ciegas se va con ella: react-query cancela con `AbortSignal`,
 * reintenta con retroceso exponencial y distingue el aborto del fallo. Es la
 * misma arquitectura que ya usaba `useRainLayer`, que nunca tuvo este bug.
 *
 * ---------------------------------------------------------------------------
 * LO QUE NO CAMBIA: la carga diferida
 * ---------------------------------------------------------------------------
 *
 *   - `enabled`    — lo que el usuario pide ahora.
 *   - `hasMounted` — si la fuente llegó a existir alguna vez. Monótona.
 *
 * `hasMounted` pasa de `false` a `true` y no vuelve. Mientras sea `false` no se
 * monta el `<Source>` y —ahora también— `useQuery` está apagado, así que el
 * archivo no se pide. Una vez montada, la fuente se queda: apagar la capa sólo
 * cambia `visibility` y el GeoJSON ya subido al worker sigue vivo.
 *
 * Atar el montaje a `enabled` funcionaría, pero cada apagado destruiría la
 * fuente y cada encendido volvería a subir la grilla. Se ve idéntico en
 * pantalla y multiplica el trabajo.
 */

export type HazardStatus = 'idle' | 'loading' | 'ready' | 'error'

export interface SeismicHazardState {
  /** Intención del usuario. **Sólo un gesto suyo la escribe.** */
  enabled: boolean
  /** ¿Debe existir el `<Source>` en el árbol? Monótono. */
  hasMounted: boolean
  status: HazardStatus
  /** Nunca `undefined`: sin datos es una rejilla vacía compartida. */
  grid: HazardGrid
  /** Celdas del modelo, para el contador del panel. */
  count: number
  toggle: () => void
  /** Reintento explícito tras un fallo. */
  retry: () => void
  /** Mensaje del fallo, si lo hubo. La fila lo muestra tal cual. */
  errorMessage: string | null
}

/**
 * El modelo se publica una vez cada varios años.
 *
 * `staleTime: Infinity` y `refetchInterval: false` no son una optimización
 * menor: sin ellos react-query volvería a pedir la grilla al recuperar el foco
 * de la pestaña y al reconectar, que es la configuración por defecto del
 * cliente de esta aplicación —correcta para incidentes en curso, absurda para
 * un archivo estático de varios megabytes.
 */
const HAZARD_QUERY_OPTIONS = {
  staleTime: Number.POSITIVE_INFINITY,
  gcTime: Number.POSITIVE_INFINITY,
  refetchInterval: false,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
} as const

export function useSeismicHazard(): SeismicHazardState {
  const [enabled, setEnabled] = useState(false)
  const [hasMounted, setHasMounted] = useState(false)

  const query = useQuery({
    queryKey: queryKeys.hazard.grid(),
    queryFn: ({ signal }) => fetchHazardGrid(signal),
    /*
     * La línea de la carga diferida. `hasMounted` y no `enabled`: una vez
     * pedido el archivo, apagar la capa no debe cancelar una descarga que ya va
     * por la mitad — volver a encenderla la reiniciaría desde cero.
     */
    enabled: hasMounted,
    ...HAZARD_QUERY_OPTIONS,
  })

  /**
   * El único escritor de `enabled`.
   *
   * `setHasMounted` va FUERA del actualizador de `setEnabled` y no dentro.
   * Meterlo dentro —como estaba— convierte el actualizador en impuro: React lo
   * invoca durante el renderizado y puede ejecutarlo dos veces en modo
   * estricto, así que un efecto secundario ahí encolaba escrituras duplicadas.
   * Nunca fue la causa del rebote, pero es la clase de detalle que convierte un
   * bug de estado en algo imposible de razonar.
   */
  const toggle = useCallback(() => {
    setHasMounted(true)
    setEnabled((current) => !current)
  }, [])

  const refetch = query.refetch
  const retry = useCallback(() => {
    setHasMounted(true)
    setEnabled(true)
    void refetch()
  }, [refetch])

  const grid = query.data ?? EMPTY_HAZARD

  /*
   * El estado deriva de la consulta, no de una máquina propia.
   *
   * Ojo con el orden: `isError` se comprueba ANTES que la ausencia de datos,
   * porque react-query conserva `data` de un intento anterior mientras
   * reintenta. Y `hasMounted` manda sobre todo lo demás: sin montar no hay
   * consulta y el estado es `idle`, no `loading`.
   */
  const status: HazardStatus = !hasMounted
    ? 'idle'
    : query.isError
      ? 'error'
      : query.data === undefined
        ? 'loading'
        : 'ready'

  const errorMessage =
    status === 'error' ? (query.error as Error | null)?.message ?? null : null

  return useMemo(
    () => ({
      enabled,
      hasMounted,
      status,
      grid,
      count: grid.cells.features.length,
      toggle,
      retry,
      errorMessage,
    }),
    [enabled, hasMounted, status, grid, toggle, retry, errorMessage],
  )
}
