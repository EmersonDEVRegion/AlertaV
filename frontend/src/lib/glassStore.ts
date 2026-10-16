import { useSyncExternalStore } from 'react'

/**
 * Cuánto cristal usa la interfaz (§D, 16-10-2026).
 *
 * Tres niveles, elegibles en el menú «⋯»: `cristal` (los valores de §T),
 * `equilibrado` (por defecto, más relleno y más contraste) y `solido` (sin
 * transparencia). Se aplica como `data-glass` en `<html>`; los valores viven
 * en `index.css` («Tres niveles de cristal»).
 *
 * Se guarda en este dispositivo. Si el almacenamiento no está (navegación
 * privada), vale el nivel por defecto y el cambio dura mientras la página esté
 * abierta. «Reducir transparencia» del sistema manda sobre cualquier nivel.
 */
export const GLASS_LEVELS = ['cristal', 'equilibrado', 'solido'] as const
export type GlassLevel = (typeof GLASS_LEVELS)[number]

export const GLASS_LABEL: Record<GlassLevel, string> = {
  cristal: 'Cristal',
  equilibrado: 'Equilibrado',
  solido: 'Sólido',
}

const KEY = 'alertav:cristal'
const DEFAULT: GlassLevel = 'equilibrado'

function read(): GlassLevel {
  try {
    const value = window.localStorage.getItem(KEY)
    return (GLASS_LEVELS as readonly string[]).includes(value ?? '') ? (value as GlassLevel) : DEFAULT
  } catch {
    return DEFAULT
  }
}

function apply(level: GlassLevel): void {
  if (typeof document !== 'undefined') document.documentElement.dataset.glass = level
}

let current: GlassLevel = typeof window === 'undefined' ? DEFAULT : read()
// Al importar el módulo, antes del primer pintado: sin parpadeo de nivel.
apply(current)

const listeners = new Set<() => void>()

export function setGlassLevel(level: GlassLevel): void {
  if (level === current) return
  current = level
  apply(level)
  try {
    window.localStorage.setItem(KEY, level)
  } catch {
    // Sin almacenamiento: dura mientras la página esté abierta.
  }
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

const snapshot = () => current

export function useGlassLevel(): GlassLevel {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}
