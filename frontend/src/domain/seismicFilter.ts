/**
 * Filtro de relevancia sísmica.
 *
 * El corte en 4.0 coincide con el que ya separa las bandas de color
 * (`MODERATE_THRESHOLD` en `seismicSymbology.ts`), y se importa de allá en vez
 * de repetirse: si un día se recalibra, el filtro y la paleta se mueven juntos.
 *
 * # El caso que decide el diseño: magnitud nula
 *
 * El USGS publica la detección antes de terminar de calcular la magnitud. Esos
 * sismos no son «menores que 4.0» ni «mayores o iguales que 4.0»: son
 * desconocidos, y ninguno de los dos filtros los describe.
 *
 * Se incluyen en **relevantes**. Es la opción precautoria: una magnitud aún no
 * calculada puede resultar alta, y esconder un sismo potencialmente fuerte de
 * la vista que el usuario eligió para ver los fuertes sería el peor error
 * posible de los dos.
 */

import type { SeismicEvent } from '@/api/seismicTypes'
import { MODERATE_THRESHOLD } from './seismicSymbology'

export const SEISMIC_FILTERS = ['relevant', 'micro'] as const
export type SeismicFilterKey = (typeof SEISMIC_FILTERS)[number]

export interface SeismicFilterOption {
  key: SeismicFilterKey
  label: string
  hint: string
}

/**
 * Por defecto, relevantes: los microsismos son decenas por día en una zona
 * sísmica y como ruido de fondo tapan lo que importa.
 */
export const DEFAULT_SEISMIC_FILTER: SeismicFilterKey = 'relevant'

export const SEISMIC_FILTER_OPTIONS: readonly SeismicFilterOption[] = [
  {
    key: 'relevant',
    label: `Relevantes (≥ ${MODERATE_THRESHOLD.toFixed(1)})`,
    hint: 'Sismos perceptibles. Incluye los que aún no tienen magnitud calculada.',
  },
  {
    key: 'micro',
    label: `Microsismos (< ${MODERATE_THRESHOLD.toFixed(1)})`,
    hint: 'Actividad de fondo, casi siempre imperceptible.',
  },
]

export function matchesSeismicFilter(
  event: SeismicEvent,
  filter: SeismicFilterKey,
): boolean {
  if (event.magnitude === null || Number.isNaN(event.magnitude)) {
    // Sin magnitud: se muestra con los relevantes, nunca con los microsismos.
    return filter === 'relevant'
  }
  return filter === 'relevant'
    ? event.magnitude >= MODERATE_THRESHOLD
    : event.magnitude < MODERATE_THRESHOLD
}

export function filterSeismic(
  events: readonly SeismicEvent[],
  filter: SeismicFilterKey,
): SeismicEvent[] {
  return events.filter((event) => matchesSeismicFilter(event, filter))
}
