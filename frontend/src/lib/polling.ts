/**
 * Cadencia de sondeo que se frena cuando el backend no responde.
 *
 * # El problema
 *
 * Con un `refetchInterval` fijo, un backend caído recibe exactamente la misma
 * presión que uno sano: cada 60 s, cada pestaña abierta dispara la consulta y
 * sus tres reintentos. Y todas las pestañas quedan sincronizadas, así que el
 * servicio que vuelve de un arranque en frío (Render duerme a los 15 min)
 * recibe las ráfagas juntas.
 *
 * # Lo que hace
 *
 * Mientras la consulta está sana, devuelve la cadencia base, sin tocarla. Con
 * una racha de errores la duplica en cada fallo (60 s → 2 → 4 → 5 min, con
 * tope) y le suma ±15 % de jitter para que las pestañas se desincronicen. El
 * primer éxito la devuelve a la base.
 *
 * # Por qué el jitter se sortea una vez por fallo
 *
 * react-query vuelve a evaluar `refetchInterval` en cada render del
 * componente y reinicia el temporizador si el valor cambió. Un `Math.random()`
 * en cada evaluación reiniciaría el reloj sin parar y el sondeo podría no
 * llegar nunca. El valor se congela por fallo y sólo cambia con el siguiente.
 */

interface Streak {
  failures: number
  /** `errorUpdatedAt` del último fallo contado: sólo cuenta los nuevos. */
  seen: number
  waitMs: number
}

/** La forma mínima de la consulta que mira: la de react-query la cumple. */
interface PolledQuery {
  state: { status: string; errorUpdatedAt: number }
}

export const POLL_BACKOFF_MAX_MS = 5 * 60_000

const streaks = new WeakMap<object, Streak>()

export function pollEvery(baseMs: number, maxMs = POLL_BACKOFF_MAX_MS) {
  return (query: PolledQuery): number => {
    const { status, errorUpdatedAt } = query.state

    if (status !== 'error') {
      streaks.delete(query)
      return baseMs
    }

    let streak = streaks.get(query)
    if (!streak) {
      streak = { failures: 0, seen: 0, waitMs: baseMs }
      streaks.set(query, streak)
    }
    if (errorUpdatedAt !== streak.seen) {
      streak.failures += 1
      streak.seen = errorUpdatedAt
      const backoff = Math.min(maxMs, baseMs * 2 ** (streak.failures - 1))
      streak.waitMs = Math.round(backoff * (0.85 + Math.random() * 0.3))
    }
    return streak.waitMs
  }
}
