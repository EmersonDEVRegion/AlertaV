import { useEffect, useSyncExternalStore } from 'react'

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

function apply(next: SheetSnap): void {
  if (next === current) return
  current = next
  for (const listener of listeners) listener()
}

export function setSheetSnap(next: SheetSnap): void {
  // Alguien la movió a propósito mientras un panel la tenía abajo: al cerrar
  // el panel ya no se le devuelve la altura de antes (ver `lowerSheet`).
  if (lowered) lowered.moved = true
  apply(next)
}

/*
 * Bajar la hoja mientras un panel de la barra está abierto (§T, 16-10-2026).
 *
 * En el teléfono, el panel de avisos, el del clima, el menú «⋯» o la lista del
 * buscador cuelgan de la barra y, con la hoja a media altura, se pisaban con
 * ella. Ahora la hoja baja animada a `peek` (su transición de `height`) y,
 * al cerrar el último panel, vuelve a la altura que tenía.
 *
 * - Varios paneles a la vez (se abre el clima con el menú abierto): se cuenta
 *   por dueño, y la hoja vuelve sólo cuando se cierra el último.
 * - Si entremedio alguien la movió —un arrastre, «Ver leyenda» del menú, una
 *   ficha que se abre— se respeta eso y no se devuelve nada.
 */
let lowered: { previous: SheetSnap; owners: Set<string>; moved: boolean } | null = null

export function lowerSheet(owner: string): void {
  if (!lowered) {
    lowered = { previous: current, owners: new Set(), moved: false }
    apply('peek')
  }
  lowered.owners.add(owner)
}

export function releaseSheet(owner: string): void {
  if (!lowered || !lowered.owners.delete(owner) || lowered.owners.size > 0) return
  const { previous, moved } = lowered
  lowered = null
  if (!moved) apply(previous)
}

/** Baja la hoja mientras `open` sea verdadero. Ver `lowerSheet`. */
export function useLowerSheetWhile(open: boolean, owner: string): void {
  useEffect(() => {
    if (!open) return
    lowerSheet(owner)
    return () => releaseSheet(owner)
  }, [open, owner])
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
  lowered = null
}
