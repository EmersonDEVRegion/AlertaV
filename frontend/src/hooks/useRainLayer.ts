import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { EMPTY_RAIN, countFloodRisk, fetchRainGeojson } from '@/api/rain'
import type { RainCollection, RainQuery } from '@/api/rainTypes'
import { env } from '@/config/env'
import { queryKeys } from '@/lib/queryClient'
import { toggleRainLayer, useRainLayerEnabled } from '@/lib/tacticalWeatherStore'

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
  /*
   * `enabled` ya no vive acá: lo posee el store del widget meteorológico.
   *
   * # Por qué se movió
   *
   * El interruptor de esta capa estaba en la tarjeta de lluvia del riel de
   * referencia, que se fusionó en el widget de la barra superior. El widget y
   * el `<Source>` del mapa son HERMANOS en el árbol, así que un `useState` acá
   * obligaría a subir la intención hasta `App` —el componente que sostiene los
   * 500 incidentes y sus cuatro particiones memorizadas— para bajarla por las
   * dos ramas. El store la reparte sin repintar nada de eso.
   *
   * # Lo que NO se movió
   *
   * La consulta. El store guarda una intención —un booleano— y este hook sigue
   * siendo el dueño de la carga diferida, la caché, los reintentos y la
   * cancelación de react-query. Meter la petición en el store habría sido
   * reimplementar peor lo que ya está resuelto, y además habría duplicado la
   * caché: `IncidentMap` y el panel leerían dos copias del mismo GeoJSON.
   *
   * # Se lee SÓLO el booleano, y eso importa
   *
   * `useRainLayerEnabled` devuelve un primitivo en vez del instantáneo completo.
   * Este hook se llama desde `App`, así que suscribirse al objeto entero
   * repintaría los 500 incidentes y sus cuatro particiones memorizadas cada diez
   * minutos, al llegar una temperatura nueva que `App` ni siquiera muestra — el
   * mismo coste que el store existe para evitar, por la puerta de atrás.
   */
  const enabled = useRainLayerEnabled()
  const [hasMounted, setHasMounted] = useState(false)

  /*
   * `hasMounted` es monótono y se enciende con el primer `enabled`. Va en un
   * efecto y no en el `toggle` porque el `toggle` ya no está acá: la intención
   * puede cambiar desde el widget sin que este hook se entere de otra forma.
   *
   * El `ref` evita el `setState` redundante en cada repintado posterior — una
   * vez montado, la comparación se resuelve sin tocar el estado de React.
   */
  const mounted = useRef(false)
  useEffect(() => {
    if (enabled && !mounted.current) {
      mounted.current = true
      setHasMounted(true)
    }
  }, [enabled])

  const query = useQuery({
    queryKey: queryKeys.rain.geojson(RAIN_PARAMS),
    queryFn: ({ signal }) => fetchRainGeojson(RAIN_PARAMS, signal),
    // La línea de la carga diferida. Sin esto, la capa costaría una llamada en
    // cada arranque aunque nadie la encienda nunca.
    enabled,
    staleTime: env.rainPollIntervalMs,
    refetchInterval: env.rainPollIntervalMs,
  })

  // Se conserva en la interfaz —lo consumen el mapa y los tests— pero delega en
  // el store, que es quien posee la intención del usuario desde que el control
  // se mudó al widget.
  const toggle = useCallback(() => toggleRainLayer(), [])

  const refetch = query.refetch
  const retry = useCallback(() => {
    if (!enabled) toggleRainLayer()
    setHasMounted(true)
    void refetch()
  }, [enabled, refetch])

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
