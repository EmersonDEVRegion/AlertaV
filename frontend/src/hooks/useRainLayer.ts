import { useCallback, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { EMPTY_RAIN, countFloodRisk, fetchRainGeojson } from '@/api/rain'
import type { RainCollection, RainQuery } from '@/api/rainTypes'
import { env } from '@/config/env'
import { queryKeys } from '@/lib/queryClient'

/**
 * Estado de la capa de lluvia pronosticada.
 *
 * # Carga diferida: dos banderas y una consulta apagada
 *
 *   - `enabled`    — lo que el usuario pide ahora.
 *   - `hasMounted` — si la fuente llegó a existir alguna vez. Monótona.
 *
 * La llamada a `/events/weather/geojson` **no ocurre hasta el primer encendido**,
 * y eso no lo consigue un `if` en el componente sino `enabled` de react-query:
 * mientras esté en `false` la consulta no se dispara, no reintenta y no aparece
 * en la red. El arranque de la aplicación no paga nada por esta capa.
 *
 * `hasMounted` hace el trabajo complementario en el mapa: una vez montado, el
 * `<Source>` se queda para siempre y apagar la capa sólo cambia `visibility`. El
 * error clásico es atar el montaje a `enabled`: funciona, pero cada apagado
 * destruye la fuente y cada encendido vuelve a subir el GeoJSON al worker.
 *
 * # Por qué el polling se detiene al apagar
 *
 * `enabled` alimenta también a `useQuery`, así que apagar la capa corta el
 * refresco. Volver a encenderla pinta al instante desde la caché y refresca
 * detrás. Es el mismo criterio que ya usa `useSeismicEvents`: no gastar red ni
 * batería trayendo datos que nadie está mirando.
 *
 * # Cadencia
 *
 * El collector corre cada 30 minutos porque los modelos globales se recalculan
 * cada 3 a 6 horas. Refrescar cada 5 minutos no aportaría un dato nuevo, sólo
 * tráfico: de ahí los 10 minutos por defecto.
 */

export type RainStatus = 'idle' | 'loading' | 'ready' | 'empty' | 'error'

export interface RainLayerState {
  enabled: boolean
  /** ¿Debe existir el `<Source>` en el árbol? Monótono. */
  hasMounted: boolean
  status: RainStatus
  /** Nunca `undefined`: el estado "soleado" es una colección vacía, no un hueco. */
  data: RainCollection
  /** Comunas con lluvia pronosticada. */
  count: number
  /** Comunas con `riesgo_inundacion`. */
  riskCount: number
  /** Atajo para no arrancar la animación cuando no hay nada que pulsar. */
  hasRisk: boolean
  toggle: () => void
  retry: () => void
}

/**
 * Sin parámetros: `hours=3` (el defecto) es holgura para cubrir una corrida que
 * llegó tarde, no una ventana de consulta, y son 36 comunas — el `limit` de 500
 * es una red de seguridad que nunca se toca. Dejarlo vacío mantiene la URL
 * estable y por tanto también la clave de caché.
 */
const RAIN_PARAMS: RainQuery = {}

export function useRainLayer(): RainLayerState {
  const [enabled, setEnabled] = useState(false)
  const [hasMounted, setHasMounted] = useState(false)

  const query = useQuery({
    queryKey: queryKeys.rain.geojson(RAIN_PARAMS),
    queryFn: ({ signal }) => fetchRainGeojson(RAIN_PARAMS, signal),
    // La línea de la carga diferida. Sin esto, la capa costaría una llamada en
    // cada arranque aunque nadie la encienda nunca.
    enabled,
    staleTime: env.rainPollIntervalMs,
    refetchInterval: env.rainPollIntervalMs,
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

  const data = query.data ?? EMPTY_RAIN
  const riskCount = useMemo(() => countFloodRisk(data), [data])

  const status: RainStatus = !hasMounted
    ? 'idle'
    : query.isError && query.data === undefined
      ? 'error'
      : query.data === undefined
        ? 'loading'
        : // Una colección vacía es una RESPUESTA CORRECTA: ninguna comuna con
          // lluvia pronosticada. El estado se llama `empty` y no `error` porque
          // el panel tiene que decir "sin lluvia pronosticada" y nunca "sin datos".
          data.features.length === 0
          ? 'empty'
          : 'ready'

  return useMemo(
    () => ({
      enabled,
      hasMounted,
      status,
      data,
      count: data.features.length,
      riskCount,
      hasRisk: riskCount > 0,
      toggle,
      retry,
    }),
    [enabled, hasMounted, status, data, riskCount, toggle, retry],
  )
}
