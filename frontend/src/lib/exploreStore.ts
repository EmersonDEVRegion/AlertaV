import { useSyncExternalStore } from 'react'

/**
 * La pestaña abierta de la columna (o de la hoja del teléfono), y el área que
 * eligió el buscador de la barra o «Mis lugares».
 *
 * Fuera de React, como la selección: la barra —el menú «⋯» y el buscador— no
 * es pariente de la columna, y pasar esto por `App` repintaría el mapa.
 */
export type ExploreTab = 'history' | 'layers' | 'legend'

/**
 * Un área para acotar el historial.
 *
 * - `commune`: lo que el feed atribuye a esa comuna (sin tildes ni mayúsculas).
 * - `place`: lo que está a menos de `NEAR_PLACE_KM` de un lugar guardado.
 */
export type ExploreArea =
  | { kind: 'commune'; name: string }
  | { kind: 'place'; id: string; name: string; lat: number; lon: number }

interface ExploreState {
  tab: ExploreTab
  area: ExploreArea | null
}

let state: ExploreState = { tab: 'history', area: null }
const listeners = new Set<() => void>()

function emit(next: ExploreState): void {
  if (next.tab === state.tab && next.area === state.area) return
  state = next
  for (const listener of listeners) listener()
}

export function setExploreTab(tab: ExploreTab): void {
  emit({ ...state, tab })
}

export function setExploreArea(area: ExploreArea | null): void {
  // Acotar por un área es mirar el historial: se muestra esa pestaña.
  emit({ tab: area ? 'history' : state.tab, area })
}

/** Si el área activa es ese lugar guardado, se quita (se borró el lugar). */
export function forgetPlaceArea(id: string): void {
  if (state.area?.kind === 'place' && state.area.id === id) emit({ ...state, area: null })
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useExploreTab(): ExploreTab {
  return useSyncExternalStore(subscribe, () => state.tab, () => state.tab)
}

export function useExploreArea(): ExploreArea | null {
  return useSyncExternalStore(subscribe, () => state.area, () => state.area)
}

/** Sólo para los tests. */
export function resetExplore(): void {
  state = { tab: 'history', area: null }
}
