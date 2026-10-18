/**
 * La última posición del teléfono que conoce la app, en memoria.
 *
 * El mapa la obtiene cuando la persona toca «mi ubicación» (con GPS fino) y
 * los avisos la necesitan para medir distancias: así una lectura sirve a los
 * dos y no se pide el GPS dos veces. No se guarda en el teléfono: al cerrar la
 * app se olvida.
 */

export interface KnownPosition {
  lat: number
  lon: number
  /** Incertidumbre en metros, si el dispositivo la informa. */
  accuracyM: number | null
  /** Cuándo se obtuvo, en ms. */
  at: number
}

let last: KnownPosition | null = null
const listeners = new Set<() => void>()

/** Informa una lectura. Una más vieja que la que ya hay se descarta. */
export function reportPosition(position: KnownPosition): void {
  if (!Number.isFinite(position.lat) || !Number.isFinite(position.lon)) return
  if (last && position.at <= last.at) return
  last = position
  for (const listener of listeners) listener()
}

export function getLastPosition(): KnownPosition | null {
  return last
}

export function subscribePosition(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/**
 * ¿El permiso de ubicación ya está concedido? Sin preguntar: un navegador sin
 * la API de permisos (o que la rechaza) cuenta como «no».
 */
export async function geolocationGranted(): Promise<boolean> {
  try {
    const status = await navigator.permissions.query({ name: 'geolocation' })
    return status.state === 'granted'
  } catch {
    return false
  }
}

/** Sólo para los tests. */
export function resetPosition(): void {
  last = null
  listeners.clear()
}
