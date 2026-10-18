import { COMPACT_BREAKPOINT } from '@/hooks/useMediaQuery'

/**
 * Ancho de la columna de escritorio, en rem. Lo usan la columna y la cámara:
 * si cambia uno sin el otro, el punto enfocado queda detrás del panel.
 */
const COLUMN_WIDTH_REM = 22

/** Alto de la hoja asomada del teléfono. Lo repite `index.css` (controles del mapa). */
export const SHEET_PEEK_REM = 7.25

/** Alto de la barra superior mientras `AppHeader` no publica el suyo. */
const HEADER_FALLBACK_REM = 3.5

export interface Insets {
  top: number
  right: number
  bottom: number
  left: number
}

function rootRem(): number {
  return parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
}

/** Alto de la barra superior, que va encima del lienzo del mapa (no sobre él). */
export function headerHeight(): number {
  if (typeof window === 'undefined') return 0
  return (
    parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--app-header-h')) ||
    HEADER_FALLBACK_REM * rootRem()
  )
}

/**
 * Lo que tapa el lienzo del mapa con todo en reposo: en el teléfono, la hoja
 * asomada abajo; en el escritorio, la columna a la izquierda. Arriba, un
 * respiro para los controles. Con esto se calcula el zoom mínimo
 * (`lib/regionCamera`): la región entera tiene que caber en lo que queda a la
 * vista.
 */
export function mapInsets(): Insets {
  if (typeof window === 'undefined') return { top: 0, right: 0, bottom: 0, left: 0 }
  const rem = rootRem()
  const compact = window.matchMedia?.(COMPACT_BREAKPOINT).matches ?? false
  if (compact) return { top: 8, right: 0, bottom: SHEET_PEEK_REM * rem, left: 0 }
  return { top: 8, right: 0, bottom: 0, left: Math.round((COLUMN_WIDTH_REM + 0.75) * rem) }
}

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

/**
 * Opciones para encuadrar el área de un corte al abrirlo. Nunca más cerca que
 * `maxZoom` (el zoom con que se enfoca cualquier incidente): un corte de una
 * cuadra no tiene por qué llenar la pantalla.
 */
export function areaFitOptions(maxZoom: number, duration: number) {
  return { padding: fitPadding(), maxZoom, duration, essential: true }
}
