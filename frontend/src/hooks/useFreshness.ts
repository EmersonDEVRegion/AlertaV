import { env } from '@/config/env'
import { useNow } from './useNow'

export interface Freshness {
  /** Milisegundos desde la última respuesta exitosa del servidor. */
  ageMs: number
  /** Supero el umbral: el dato puede no describir el presente. */
  isStale: boolean
  /** Nunca hubo una respuesta exitosa en esta sesion. */
  isUnknown: boolean
}

/**
 * Cada cuánto se reevalúa la edad. El umbral es de minutos
 * (`VITE_STALE_AFTER_MS`, 3 min por defecto) y el texto dice «hace X min»: un
 * tic por segundo no mostraba nada distinto.
 */
const FRESHNESS_TICK_MS = 5_000

/**
 * Edad real del dato en pantalla.
 *
 * Es el mecanismo que sostiene la decisión de cachear offline: el service worker
 * puede servir una respuesta de hace minutos, y esto obliga a que la interfaz lo
 * diga en vez de presentarla como si fuera de ahora.
 *
 * **Se llama sólo desde `StalenessBanner`.** Vivía en `App`, y su tic repintaba
 * la aplicación entera —mapa incluido— sesenta veces por minuto.
 */
export function useFreshness(dataUpdatedAt: number | undefined): Freshness {
  const now = useNow(FRESHNESS_TICK_MS)

  if (!dataUpdatedAt) {
    return { ageMs: 0, isStale: false, isUnknown: true }
  }

  const ageMs = Math.max(0, now - dataUpdatedAt)
  return { ageMs, isStale: ageMs > env.staleAfterMs, isUnknown: false }
}
