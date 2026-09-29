/**
 * Qué está seleccionado en el visor: un incidente, un sismo, el radar o nada.
 *
 * # Por qué no vive en `App`
 *
 * La selección es el estado más efímero de la aplicación —cambia con cada
 * toque— y vivía en el mismo componente que sostiene los 500 incidentes, sus
 * particiones y los dos paneles. Cada toque en un pin repintaba la barra, los
 * paneles y el mapa para mover un anillo.
 *
 * Con un store externo y `useSyncExternalStore`, cada componente se suscribe
 * sólo al pedazo que usa y recibe un PRIMITIVO: la capa del anillo lee un
 * código, la ficha lee un código, el botón del radar lee un booleano. React
 * compara primitivos con `Object.is`, así que quien no depende de lo que cambió
 * no se entera. Es el mismo patrón que `tacticalWeatherStore`.
 *
 * # Exclusión mutua por construcción
 *
 * Antes eran dos `useState` más un booleano del radar, y la regla «abrir uno
 * cierra los otros» vivía repartida en tres manejadores y un `useEffect`. Acá es
 * un solo valor: no hay forma de representar un incidente y el radar abiertos
 * a la vez. Una capa nueva que se seleccione (los cortes de agua, por ejemplo)
 * entra como una variante más de `Selection`, no como otro `useState`.
 */

import { useSyncExternalStore } from 'react'

export type Selection =
  | { readonly kind: 'none' }
  | { readonly kind: 'incident'; readonly code: string }
  | { readonly kind: 'seismic'; readonly usgsId: string }
  | { readonly kind: 'radar' }

const NONE: Selection = Object.freeze({ kind: 'none' })

let current: Selection = NONE
const listeners = new Set<() => void>()

function same(a: Selection, b: Selection): boolean {
  if (a.kind !== b.kind) return false
  if (a.kind === 'incident' && b.kind === 'incident') return a.code === b.code
  if (a.kind === 'seismic' && b.kind === 'seismic') return a.usgsId === b.usgsId
  return true
}

/** Cambia la selección. Si no cambia nada, no avisa a nadie. */
export function select(next: Selection): void {
  if (same(current, next)) return
  current = next.kind === 'none' ? NONE : Object.freeze({ ...next })
  for (const listener of listeners) listener()
}

export function selectIncident(code: string): void {
  select({ kind: 'incident', code })
}

export function selectSeismic(usgsId: string): void {
  select({ kind: 'seismic', usgsId })
}

export function clearSelection(): void {
  select(NONE)
}

/** Cierra sólo si lo seleccionado es de ese tipo. Para los botones de cerrar. */
export function clearIf(kind: Selection['kind']): void {
  if (current.kind === kind) select(NONE)
}

export function getSelection(): Selection {
  return current
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** La selección completa. Para quien de verdad necesita saber de qué tipo es. */
export function useSelection(): Selection {
  return useSyncExternalStore(subscribe, getSelection, getSelection)
}

const incidentCode = () => (current.kind === 'incident' ? current.code : null)
const seismicId = () => (current.kind === 'seismic' ? current.usgsId : null)
const radarOpen = () => current.kind === 'radar'

/** Folio del incidente seleccionado, o `null`. */
export function useSelectedIncidentCode(): string | null {
  return useSyncExternalStore(subscribe, incidentCode, incidentCode)
}

/** Id USGS/CSN del sismo seleccionado, o `null`. */
export function useSelectedSeismicId(): string | null {
  return useSyncExternalStore(subscribe, seismicId, seismicId)
}

export function useRadarOpen(): boolean {
  return useSyncExternalStore(subscribe, radarOpen, radarOpen)
}

/** Deja el store como al arrancar. **Sólo para los tests.** */
export function resetSelection(): void {
  current = NONE
  listeners.clear()
}
