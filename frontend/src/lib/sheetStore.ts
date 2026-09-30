import { useSyncExternalStore } from 'react'

/**
 * Altura de la hoja inferior del teléfono.
 *
 * Vive fuera de React por la misma razón que la selección (`selectionStore`):
 * la leen piezas que no son parientes —la hoja, el botón de reporte y la
 * cámara— y subirla a `App` repintaría el mapa en cada arrastre.
 *
 * - `peek`: asoma el resumen y las pestañas; el mapa queda casi entero.
 * - `half`: la lista o la ficha a media pantalla, con el mapa arriba.
 * - `full`: la lista completa; el mapa queda detrás.
 */
export type SheetSnap = 'peek' | 'half' | 'full'

let current: SheetSnap = 'peek'
const listeners = new Set<() => void>()

export function setSheetSnap(next: SheetSnap): void {
  if (next === current) return
  current = next
  for (const listener of listeners) listener()
}

export function getSheetSnap(): SheetSnap {
  return current
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useSheetSnap(): SheetSnap {
  return useSyncExternalStore(subscribe, getSheetSnap, getSheetSnap)
}

/** Sólo para los tests: sin avisar, porque corre después de desmontar. */
export function resetSheet(): void {
  current = 'peek'
}
