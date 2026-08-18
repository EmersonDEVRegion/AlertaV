import { useEffect, useState } from 'react'
import { env } from '@/config/env'

export interface Freshness {
  /** Milisegundos desde la última respuesta exitosa del servidor. */
  ageMs: number
  /** Supero el umbral: el dato puede no describir el presente. */
  isStale: boolean
  /** Nunca hubo una respuesta exitosa en esta sesion. */
  isUnknown: boolean
}

/**
 * Edad real del dato en pantalla.
 *
 * Es el mecanismo que sostiene la decisión de cachear offline: el service worker
 * puede servir una respuesta de hace minutos, y esto obliga a que la interfaz lo
 * diga en vez de presentarla como si fuera de ahora. Se recalcula cada segundo
 * porque el dato envejece aunque no pase nada más en la aplicacion.
 */
export function useFreshness(dataUpdatedAt: number | undefined): Freshness {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])

  if (!dataUpdatedAt) {
    return { ageMs: 0, isStale: false, isUnknown: true }
  }

  const ageMs = Math.max(0, now - dataUpdatedAt)
  return { ageMs, isStale: ageMs > env.staleAfterMs, isUnknown: false }
}
