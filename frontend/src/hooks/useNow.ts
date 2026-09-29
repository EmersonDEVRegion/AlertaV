import { useCallback, useSyncExternalStore } from 'react'

/**
 * La hora actual, refrescada cada `intervalMs`.
 *
 * Existe para lo que envejece sin que llegue nada nuevo: el «hace 3 h» de una
 * tarjeta y la ventana de 48 h del radar. Sin esto, una pestaña abierta toda la
 * tarde seguiría diciendo «hace 5 min» de un aviso de hace horas, y seguiría
 * mostrando uno que ya salió de la ventana.
 *
 * # Un reloj por cadencia, compartido
 *
 * Todos los componentes que piden la misma cadencia leen el MISMO reloj: un
 * solo intervalo, que existe mientras alguien lo mira y se apaga con el último.
 * Cien filas de una lista no son cien temporizadores.
 *
 * # Por qué el reloj se pide donde se usa
 *
 * Antes estos «hace X» se refrescaban de rebote: `useFreshness` vivía en `App`
 * y lo volvía a renderizar entero cada segundo —el mapa, los dos paneles y la
 * barra— sólo para mover el cartel de antigüedad. Esas etiquetas dependían sin
 * saberlo de ese tic. Ahora cada componente que muestra una edad pide su propio
 * reloj, y lo que no muestra edades no se entera de que el tiempo pasa.
 */

interface Clock {
  now: number
  listeners: Set<() => void>
  timer: ReturnType<typeof setInterval> | undefined
}

const clocks = new Map<number, Clock>()

function clockFor(intervalMs: number): Clock {
  let clock = clocks.get(intervalMs)
  if (!clock) {
    clock = { now: Date.now(), listeners: new Set(), timer: undefined }
    clocks.set(intervalMs, clock)
  }
  return clock
}

function subscribeClock(intervalMs: number, listener: () => void): () => void {
  const clock = clockFor(intervalMs)
  clock.listeners.add(listener)

  if (clock.timer === undefined) {
    // Un reloj que estuvo detenido no puede reanudar con la hora de cuando se
    // detuvo: el primer suscriptor lo pone al día.
    clock.now = Date.now()
    clock.timer = setInterval(() => {
      clock.now = Date.now()
      for (const notify of clock.listeners) notify()
    }, intervalMs)
  }

  return () => {
    clock.listeners.delete(listener)
    if (clock.listeners.size === 0 && clock.timer !== undefined) {
      clearInterval(clock.timer)
      clock.timer = undefined
    }
  }
}

/**
 * Cadencia de las etiquetas «hace X». La unidad más fina que muestran es el
 * minuto: medio minuto basta para que nunca se atrasen uno entero.
 */
export const RELATIVE_TIME_TICK_MS = 30_000

export function useNow(intervalMs = 60_000): number {
  const subscribe = useCallback(
    (listener: () => void) => subscribeClock(intervalMs, listener),
    [intervalMs],
  )
  const getSnapshot = useCallback(() => clockFor(intervalMs).now, [intervalMs])
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}
