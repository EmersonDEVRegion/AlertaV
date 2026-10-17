/**
 * Colores de los marcadores que dependen del tema.
 *
 * Todo marcador del mapa —incidentes, cortes de luz y de agua, sismos, pin
 * fantasma— usa los mismos dos: el borde o la placa toman el color de fondo del
 * tema (blanco en claro, pizarra casi negra en oscuro) y la selección, el de
 * máximo contraste. Un blanco fijo sobre el mapa oscuro es una mancha; un anillo
 * de selección `#0f172a` sobre el mapa oscuro, invisible.
 *
 * El mismo `PIN_EDGE` vive en CSS como `--pin-edge` (`index.css`), para las
 * muestras de la leyenda y de la ficha.
 */

import type { Theme } from '@/hooks/useTheme'

/** Borde del disco o placa del marcador: el fondo del tema. */
export const PIN_EDGE: Record<Theme, string> = { light: '#ffffff', dark: '#0f172a' }
/** Anillo de selección: el color de máximo contraste del tema. */
export const PIN_INK: Record<Theme, string> = { light: '#0f172a', dark: '#f8fafc' }
