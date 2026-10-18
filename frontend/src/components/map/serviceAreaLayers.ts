/**
 * Áreas afectadas de los cortes de luz y de agua.
 *
 * Dos niveles, como el círculo de incertidumbre de los incidentes:
 *
 * - **El corte abierto**: su área se ve a cualquier zoom, con relleno y borde
 *   marcados. Es la respuesta a «¿me toca a mí?» cuando uno toca el pin.
 * - **Los demás**: desde `AREA_MIN_ZOOM` (barrio), tenues, para ver dónde no hay
 *   luz o agua sin tocar nada. Más lejos serían manchas que tapan el mapa y los
 *   racimos ya dicen lo que importa a esa escala.
 *
 * Los cortes cerrados no muestran área salvo abiertos: ya no hay nadie sin luz.
 * Van por debajo de todos los pines (ver el orden en `IncidentMap`).
 */

import type {
  ExpressionSpecification,
  FillLayerSpecification,
  FilterSpecification,
  LineLayerSpecification,
} from 'maplibre-gl'
import { PROVIDER, UNKNOWN_PROVIDER } from '@/domain/powerSymbology'
import { AREA_LINE, AREA_MIN_ZOOM } from '@/domain/serviceArea'
import { WATER } from '@/domain/waterSymbology'
import type { Theme } from '@/hooks/useTheme'

type FillLayer = Omit<FillLayerSpecification, 'source'>
type LineLayer = Omit<LineLayerSpecification, 'source'>

export const SERVICE_AREA_SOURCE_ID = 'service-areas'

const FILL_COLOR = [
  'match',
  ['get', 'kind'],
  'chilquinta',
  PROVIDER.chilquinta.color,
  'cge',
  PROVIDER.cge.color,
  'water',
  WATER.color,
  UNKNOWN_PROVIDER.color,
] as unknown as ExpressionSpecification

function lineColor(theme: Theme): ExpressionSpecification {
  return [
    'match',
    ['get', 'kind'],
    'chilquinta',
    AREA_LINE.chilquinta[theme],
    'cge',
    AREA_LINE.cge[theme],
    'water',
    AREA_LINE.water[theme],
    AREA_LINE.unknown[theme],
  ] as unknown as ExpressionSpecification
}

/** Aparece en medio nivel de zoom en vez de saltar. */
function fadeIn(max: number): ExpressionSpecification {
  return ['interpolate', ['linear'], ['zoom'], AREA_MIN_ZOOM - 0.5, 0, AREA_MIN_ZOOM + 0.5, max]
}

const OPEN_ONLY: FilterSpecification = ['!', ['get', 'is_closed']]

function selectedFilter(ids: readonly string[]): FilterSpecification {
  // Sin selección, un filtro que no calza con nada (y no la capa desmontada:
  // así el ancla de las capas de encima existe siempre).
  return ['in', ['get', 'area_id'], ['literal', ids.length ? [...ids] : ['']]]
}

export function serviceAreaFillLayer(): FillLayer {
  return {
    id: 'service-areas-fill',
    type: 'fill',
    minzoom: AREA_MIN_ZOOM - 0.5,
    filter: OPEN_ONLY,
    paint: { 'fill-color': FILL_COLOR, 'fill-opacity': fadeIn(0.12) },
  }
}

export function serviceAreaLineLayer(theme: Theme): LineLayer {
  return {
    id: 'service-areas-line',
    type: 'line',
    minzoom: AREA_MIN_ZOOM - 0.5,
    filter: OPEN_ONLY,
    layout: { 'line-join': 'round' },
    paint: { 'line-color': lineColor(theme), 'line-width': 1, 'line-opacity': fadeIn(0.6) },
  }
}

export function serviceAreaSelectedFillLayer(ids: readonly string[]): FillLayer {
  return {
    id: 'service-areas-selected-fill',
    type: 'fill',
    filter: selectedFilter(ids),
    paint: { 'fill-color': FILL_COLOR, 'fill-opacity': 0.22 },
  }
}

export function serviceAreaSelectedLineLayer(theme: Theme, ids: readonly string[]): LineLayer {
  return {
    id: 'service-areas-selected-line',
    type: 'line',
    filter: selectedFilter(ids),
    layout: { 'line-join': 'round' },
    paint: { 'line-color': lineColor(theme), 'line-width': 2, 'line-opacity': 0.9 },
  }
}
