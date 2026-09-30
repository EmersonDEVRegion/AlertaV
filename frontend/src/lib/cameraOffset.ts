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
