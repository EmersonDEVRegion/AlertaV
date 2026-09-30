import { COMPACT_BREAKPOINT } from '@/hooks/useMediaQuery'

/**
 * Ancho de la columna de escritorio, en rem. Lo usan la columna y la cámara:
 * si cambia uno sin el otro, el punto enfocado queda detrás del panel.
 */
const COLUMN_WIDTH_REM = 22

/**
 * Desplazamiento de la cámara al enfocar algo, para que el punto no quede
 * detrás de lo que tapa el mapa.
 *
 * - Escritorio: la columna ocupa el borde izquierdo, así que el centro útil se
 *   corre hacia la derecha la mitad de su ancho.
 * - Teléfono: la ficha abre la hoja a media pantalla, así que el centro útil
 *   sube una cuarta parte del alto.
 */
export function focusOffset(): [number, number] {
  if (typeof window === 'undefined') return [0, 0]
  const compact = window.matchMedia?.(COMPACT_BREAKPOINT).matches ?? false
  if (compact) return [0, -Math.round(window.innerHeight * 0.22)]
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  return [Math.round(((COLUMN_WIDTH_REM + 0.75) * rem) / 2), 0]
}

/**
 * Margen para encuadrar una caja (una comuna) sin que quede detrás de lo que
 * tapa el mapa: la columna en escritorio, la hoja a media altura en teléfono.
 */
export function fitPadding(): { top: number; right: number; bottom: number; left: number } {
  if (typeof window === 'undefined') return { top: 40, right: 40, bottom: 40, left: 40 }
  const compact = window.matchMedia?.(COMPACT_BREAKPOINT).matches ?? false
  if (compact) {
    return { top: 24, right: 24, bottom: Math.round(window.innerHeight * 0.5), left: 24 }
  }
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  return { top: 48, right: 48, bottom: 48, left: Math.round((COLUMN_WIDTH_REM + 0.75) * rem) + 32 }
}
