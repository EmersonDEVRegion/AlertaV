/**
 * Qué muestra el mapa al abrir la app.
 *
 * Quien abre una app de emergencias pregunta primero «¿pasa algo cerca de
 * mí?», y después «¿qué está pasando en la región?». Por eso, en orden:
 *
 * 1. **Un enlace** (aviso tocado, `/i/…`): manda él; esto no hace nada.
 * 2. **Dónde estoy**, si el permiso de ubicación ya está concedido: nunca se
 *    pregunta al abrir (un diálogo de permiso antes de ver el mapa es la peor
 *    bienvenida). Al zoom de «Mis lugares», que muestra ~5 km alrededor: el
 *    radio de los avisos de incendio.
 * 3. **Casa** (o el primer lugar guardado), si no hay ubicación.
 * 4. **La región entera**: alejarse al máximo siempre vuelve a esta vista.
 *
 * Fuera de la región (alguien en Santiago) no se va a su ubicación: el mapa
 * quedaría tapado por la máscara.
 */

import { nearRegion } from '@/domain/region'
import { distanceKm } from './geo'
import type { SavedPlace } from './placesStore'
import type { RegionCamera } from './regionCamera'

/** Zoom de «Mis lugares»: se ve el radio de 5 km alrededor. */
export const NEARBY_ZOOM = 12

/** Más allá de esto, la lectura del GPS llega tarde: ya se está mirando otra cosa. */
export const POSITION_DEADLINE_MS = 8_000

export interface StartView {
  kind: 'region' | 'place'
  longitude: number
  latitude: number
  zoom: number
}

/** El lugar que representa «mi casa»: el que se llama Casa, o el primero. */
export function homePlace(places: readonly SavedPlace[]): SavedPlace | null {
  return (
    places.find((p) => p.name.trim().toLocaleLowerCase('es') === 'casa') ?? places[0] ?? null
  )
}

/** La vista con que nace el mapa (antes de saber la ubicación). */
export function startView(places: readonly SavedPlace[], camera: RegionCamera): StartView {
  const home = homePlace(places)
  if (home && nearRegion(home.lat, home.lon)) {
    return { kind: 'place', longitude: home.lon, latitude: home.lat, zoom: NEARBY_ZOOM }
  }
  return { kind: 'region', ...camera.center, zoom: camera.minZoom }
}

/**
 * ¿Ir a la ubicación que acaba de llegar?
 *
 * Sólo si nadie movió el mapa mientras tanto (`untouched`), llegó a tiempo,
 * está en la región y queda a más de 1 km de lo que ya se ve al centro.
 */
export function shouldGoToPosition(input: {
  position: { lat: number; lon: number }
  untouched: boolean
  elapsedMs: number
  start: StartView
}): boolean {
  const { position, untouched, elapsedMs, start } = input
  if (!untouched || elapsedMs > POSITION_DEADLINE_MS) return false
  if (!nearRegion(position.lat, position.lon)) return false
  if (start.kind === 'region') return true
  return distanceKm(position.lat, position.lon, start.latitude, start.longitude) > 1
}
