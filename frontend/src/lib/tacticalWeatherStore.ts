/**
 * Estado meteorológico táctico: una fuente de verdad **fuera** de React.
 *
 * ===========================================================================
 * POR QUÉ ESTO NO ES UN HOOK DE react-query COMO LAS DEMÁS CAPAS
 * ===========================================================================
 *
 * `useRainLayer`, `useSeismicHazard` y `useRoadClosures` son hooks de
 * react-query y está bien que lo sean: los tres alimentan **una** superficie,
 * arrancan apagados y viven dentro del árbol que los usa. Éste no cumple
 * ninguna de las tres condiciones, y cada incumplimiento cuesta algo concreto.
 *
 * 1. Tiene DOS consumidores en ramas distintas del árbol
 * ------------------------------------------------------
 *
 * El widget vive en `AppHeader`; el interruptor de la capa de lluvia que el
 * widget contiene gobierna un `<Source>` que vive dentro de `IncidentMap`. Son
 * hermanos, no ascendiente y descendiente. Con un hook, el estado tendría que
 * subir a `App` para poder bajar por las dos ramas — y `App` es el componente
 * que sostiene los 500 incidentes, sus cuatro `useMemo` de partición y la
 * referencia del mapa. **Un `setState` ahí repinta ese árbol entero.**
 *
 * Cada diez minutos, para actualizar una temperatura. El store deja que sólo
 * quien se suscribe se entere.
 *
 * 2. El árbol que lo contiene se DESMONTA al girar el teléfono
 * ------------------------------------------------------------
 *
 * `useIsCompact` no cambia estilos: cambia el árbol. Cruzar los 768 px desmonta
 * `MobileMapControls` y monta el riel de escritorio, o al revés. Un estado
 * guardado en un hook de ese subárbol se reinicia en ese momento — el widget se
 * quedaría en «consultando…» y volvería a golpear la API por girar el
 * dispositivo. El temporizador de este módulo sobrevive porque nunca estuvo
 * dentro de React.
 *
 * 3. `getSnapshot` se lee ANTES de pintar
 * ----------------------------------------
 *
 * Es la razón de fondo, la misma que documenta `useMediaQuery`. Con
 * `useState` + `useEffect`, el primer cuadro usa siempre el valor por defecto y
 * el layout salta cuando el efecto corre. En la barra superior eso es un widget
 * que aparece a mitad de una cápsula ya pintada y empuja la telemetría hacia la
 * izquierda. `useSyncExternalStore` consulta el instantáneo durante el
 * renderizado y no hay salto.
 *
 * ===========================================================================
 * LA REGLA QUE NO SE PUEDE ROMPER: `getSnapshot` DEVUELVE LA MISMA REFERENCIA
 * ===========================================================================
 *
 * React llama a `getSnapshot()` en cada renderizado y compara por identidad con
 * `Object.is`. Si devolviera un objeto nuevo cada vez, React vería un cambio
 * perpetuo y renderizaría en bucle hasta el «Maximum update depth exceeded».
 *
 * Por eso el instantáneo es un objeto **congelado que sólo se sustituye cuando
 * algo cambió de verdad** (`snapshot`), y por eso `UNKNOWN_WEATHER` es una
 * constante compartida y no un literal. Es el mismo cuidado que `EMPTY_RAIN`
 * tiene con la identidad del GeoJSON, por un motivo distinto.
 *
 * ===========================================================================
 * CICLO DE VIDA
 * ===========================================================================
 *
 * El temporizador se enciende con el primer suscriptor y se apaga con el
 * último: un widget que nadie mira no gasta batería. Y se pausa con la pestaña
 * en segundo plano, igual que `refetchIntervalInBackground: false` en
 * `queryClient` — un teléfono en el bolsillo no necesita el pronóstico.
 */

import { useSyncExternalStore } from 'react'
import { UNKNOWN_WEATHER, fetchTacticalWeather } from '@/api/tacticalWeather'
import type { TacticalWeather } from '@/api/tacticalWeatherTypes'
import { env } from '@/config/env'

export type WeatherStatus = 'loading' | 'ready' | 'error'

export interface WeatherSnapshot {
  status: WeatherStatus
  /** Nunca `null`: el estado desconocido es un valor, no un hueco. */
  data: TacticalWeather
  /** `Date.now()` de la última respuesta correcta. `null` si nunca hubo una. */
  updatedAt: number | null
  /** ¿Está el detalle desplegado? Vive acá para sobrevivir al cambio de árbol. */
  expanded: boolean
  /** ¿Está encendida la capa de lluvia del mapa? */
  rainLayer: boolean
}

/**
 * Instantáneo inicial. Congelado y compartido: es el que devuelve
 * `getServerSnapshot` y el que se lee en el primer renderizado, antes de que
 * exista ninguna respuesta.
 */
const INITIAL: WeatherSnapshot = Object.freeze({
  status: 'loading' as const,
  data: UNKNOWN_WEATHER,
  updatedAt: null,
  expanded: false,
  rainLayer: false,
})

let snapshot: WeatherSnapshot = INITIAL
const listeners = new Set<() => void>()

let timer: ReturnType<typeof setInterval> | null = null
let inFlight: AbortController | null = null

function emit(): void {
  for (const listener of listeners) listener()
}

/**
 * Sustituye el instantáneo y avisa.
 *
 * Crea un objeto nuevo **sólo acá**, que es lo que mantiene la identidad
 * estable entre cambios reales. Ver la regla de `getSnapshot` en el encabezado.
 */
function patch(cambios: Partial<WeatherSnapshot>): void {
  snapshot = Object.freeze({ ...snapshot, ...cambios })
  emit()
}

async function refresh(): Promise<void> {
  // Una consulta en vuelo se cancela antes de lanzar otra. Sin esto, una
  // respuesta lenta podría llegar DESPUÉS de una rápida posterior y dejar el
  // widget mostrando el estado anterior — el clásico problema de carrera que en
  // una barra de alertas significa esconder una alerta que ya llegó.
  inFlight?.abort()
  const controller = new AbortController()
  inFlight = controller

  try {
    const data = await fetchTacticalWeather(controller.signal)
    if (controller.signal.aborted) return
    patch({ status: 'ready', data, updatedAt: Date.now() })
  } catch (error) {
    if (controller.signal.aborted) return
    if (error instanceof DOMException && error.name === 'AbortError') return

    // Se conserva el último dato bueno y sólo cambia el estado. Un fallo de red
    // pasajero no debe borrar la temperatura de la barra: `updatedAt` ya dice
    // cuán vieja es, y la interfaz puede envejecerla en vez de vaciarla.
    patch({ status: 'error' })
    console.warn('[AlertaV/meteo] no se pudo leer el estado táctico', error)
  } finally {
    if (inFlight === controller) inFlight = null
  }
}

function onVisibilityChange(): void {
  if (document.visibilityState === 'visible') void refresh()
}

function start(): void {
  if (timer !== null) return
  void refresh()
  timer = setInterval(() => {
    // El intervalo sigue corriendo con la pestaña oculta —los navegadores ya lo
    // estrangulan— pero la consulta no sale. Al volver, `visibilitychange`
    // fuerza una lectura inmediata en vez de esperar hasta diez minutos con un
    // dato viejo en pantalla.
    if (document.visibilityState === 'visible') void refresh()
  }, env.weatherPollIntervalMs)
  document.addEventListener('visibilitychange', onVisibilityChange)
}

function stop(): void {
  if (timer !== null) clearInterval(timer)
  timer = null
  document.removeEventListener('visibilitychange', onVisibilityChange)
  inFlight?.abort()
  inFlight = null
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (listeners.size === 1) start()
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) stop()
  }
}

function getSnapshot(): WeatherSnapshot {
  return snapshot
}

/**
 * Instantáneo del servidor. No hay SSR en esta aplicación, pero
 * `useSyncExternalStore` lo exige y devolver el inicial es la respuesta correcta
 * para cualquier entorno sin `document` — jsdom incluido, donde los tests montan
 * el widget sin que ninguna consulta haya salido.
 */
function getServerSnapshot(): WeatherSnapshot {
  return INITIAL
}

/* ------------------------------------------------------------------------- */
/* API pública                                                                */
/* ------------------------------------------------------------------------- */

/** Despliega o pliega el detalle. */
export function toggleWeatherDetail(): void {
  patch({ expanded: !snapshot.expanded })
}

export function closeWeatherDetail(): void {
  if (snapshot.expanded) patch({ expanded: false })
}

/**
 * Enciende o apaga la capa de lluvia del mapa.
 *
 * El interruptor vive acá y no en `useRainLayer` porque el control se movió al
 * widget cuando la tarjeta aislada de lluvia desapareció del riel de
 * referencia. `useRainLayer` sigue siendo dueño de la CONSULTA —con su carga
 * diferida y su caché— y este booleano es sólo la intención del usuario, que es
 * lo que el hook necesita saber para encenderse.
 */
export function toggleRainLayer(): void {
  patch({ rainLayer: !snapshot.rainLayer })
}

/** Fuerza una relectura. Lo usa el botón de reintento del detalle. */
export function retryWeather(): void {
  patch({ status: 'loading' })
  void refresh()
}

/**
 * Restablece el módulo. **Sólo para los tests.**
 *
 * Un store a nivel de módulo sobrevive entre casos dentro del mismo archivo, y
 * un test que deje datos cargados haría pasar al siguiente por el motivo
 * equivocado.
 */
export function __resetWeatherStore(): void {
  stop()
  listeners.clear()
  snapshot = INITIAL
}

export function useTacticalWeather(): WeatherSnapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}

/**
 * Sólo el interruptor de la capa de lluvia.
 *
 * # Por qué existe si `useTacticalWeather` ya lo devuelve
 *
 * Porque quien necesita este booleano es `useRainLayer`, y `useRainLayer` se
 * llama desde `App` — el componente que sostiene los 500 incidentes y sus cuatro
 * particiones memorizadas. Con el instantáneo completo, **cada lectura del
 * pronóstico repintaría ese árbol entero**: `patch()` crea un objeto nuevo, React
 * lo compara por identidad, y `App` se renderiza cada diez minutos para
 * actualizar una temperatura que ni siquiera muestra. Sería el mismo coste que
 * el store se puso a evitar, sólo que por la puerta de atrás.
 *
 * Devolver un **primitivo** lo resuelve sin selectores ni memorización:
 * `Object.is(false, false)` es verdadero, así que React no reprograma nada
 * mientras el booleano no cambie de verdad. Es la ventaja de
 * `useSyncExternalStore` que suele desaprovecharse — el instantáneo no tiene por
 * qué ser todo el estado.
 */
export function useRainLayerEnabled(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => snapshot.rainLayer,
    () => INITIAL.rainLayer,
  )
}
