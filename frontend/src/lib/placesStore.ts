import { useSyncExternalStore } from 'react'

/**
 * Lugares guardados: «Casa», «Trabajo».
 *
 * Viven en este teléfono (`localStorage`), no en una cuenta: AlertaV no tiene
 * cuentas. Si los avisos están activos, `usePushNotifications` se los manda al
 * servidor junto con la suscripción para avisar de lo que pase cerca de ellos
 * aunque el teléfono esté en otra parte; el servidor los guarda redondeados
 * (~110 m) y los borra al desactivar los avisos.
 *
 * Fuera de React por lo de siempre: los leen el mapa (marcadores), la columna
 * («Mis lugares»), el buscador y el hook de avisos, que no son parientes.
 */
export interface SavedPlace {
  id: string
  name: string
  lat: number
  lon: number
}

export const MAX_PLACES = 3
/** Radio de «cerca de Casa»: el mismo de los avisos. */
export const NEAR_PLACE_KM = 5

const KEY = 'alertav:lugares'
const EMPTY: readonly SavedPlace[] = []

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

function isPlace(value: unknown): value is SavedPlace {
  const p = value as Partial<SavedPlace> | null
  return (
    typeof p?.id === 'string' &&
    typeof p.name === 'string' &&
    p.name.trim().length > 0 &&
    typeof p.lat === 'number' &&
    typeof p.lon === 'number' &&
    Number.isFinite(p.lat) &&
    Number.isFinite(p.lon)
  )
}

function load(): readonly SavedPlace[] {
  try {
    const raw = storage()?.getItem(KEY)
    if (!raw) return EMPTY
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return EMPTY
    return parsed.filter(isPlace).slice(0, MAX_PLACES)
  } catch {
    return EMPTY
  }
}

let places: readonly SavedPlace[] = load()
const listeners = new Set<() => void>()

function commit(next: readonly SavedPlace[]): void {
  places = next
  try {
    if (next.length === 0) storage()?.removeItem(KEY)
    else storage()?.setItem(KEY, JSON.stringify(next))
  } catch {
    // Sin almacenamiento (modo privado): los lugares duran lo que la pestaña.
  }
  for (const listener of listeners) listener()
}

function newId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
}

/** Guarda un lugar. `null` si ya hay `MAX_PLACES`. */
export function addPlace(name: string, lat: number, lon: number): SavedPlace | null {
  if (places.length >= MAX_PLACES) return null
  const clean = name.replace(/\s+/g, ' ').trim().slice(0, 40) || 'Mi lugar'
  const place: SavedPlace = { id: newId(), name: clean, lat, lon }
  commit([...places, place])
  return place
}

export function removePlace(id: string): void {
  if (!places.some((p) => p.id === id)) return
  commit(places.filter((p) => p.id !== id))
}

export function getPlaces(): readonly SavedPlace[] {
  return places
}

function subscribePlaces(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function usePlaces(): readonly SavedPlace[] {
  return useSyncExternalStore(subscribePlaces, getPlaces, getPlaces)
}

// ---------------------------------------------------------------------------
// Elegir en el mapa
// ---------------------------------------------------------------------------

/** El nombre del lugar que se está ubicando en el mapa, o `null`. */
let picking: string | null = null
const pickListeners = new Set<() => void>()

function setPicking(next: string | null): void {
  if (next === picking) return
  picking = next
  for (const listener of pickListeners) listener()
}

export function startPicking(name: string): void {
  setPicking(name)
}

export function stopPicking(): void {
  setPicking(null)
}

export function usePicking(): string | null {
  return useSyncExternalStore(
    (listener) => {
      pickListeners.add(listener)
      return () => pickListeners.delete(listener)
    },
    () => picking,
    () => picking,
  )
}

/** Sólo para los tests. */
export function resetPlaces(): void {
  places = load()
  picking = null
}
