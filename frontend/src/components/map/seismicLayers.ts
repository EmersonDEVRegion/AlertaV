/**
 * Capas de MapLibre para los sismos.
 *
 * Separadas de `incidentLayers.ts` a propósito: distinta fuente, distinta
 * escala y distinta forma. Los sismos son **círculos huecos de trazo grueso**;
 * los incidentes, discos sólidos. Ese contraste de forma es lo que permite
 * distinguir las dos capas de un vistazo aunque ambas paletas sean cálidas.
 *
 *   1. `seismic-ring`  — el círculo. Radio por magnitud, color por banda.
 *   2. `seismic-core`  — punto central. Marca el epicentro exacto, que en un
 *                        círculo grande queda ambiguo.
 *   3. `seismic-hit`   — objetivo táctil invisible.
 */

import type { CircleLayerSpecification, ExpressionSpecification } from 'maplibre-gl'
import {
  MAGNITUDE_COLOR_EXPRESSION,
  MAX_SIZED_MAGNITUDE,
  MIN_SIZED_MAGNITUDE,
} from '@/domain/seismicSymbology'

export type SeismicLayer = Omit<CircleLayerSpecification, 'source'>

export const SEISMIC_SOURCE_ID = 'seismic'
export const SEISMIC_HIT_LAYER_ID = 'seismic-hit'

/**
 * Radio: interpolación lineal sobre la magnitud acotada, escalada por zoom.
 *
 * `sizing_magnitude` ya viene acotada a [2, 7] desde el cliente, así que acá no
 * se repite esa regla: vive una sola vez, en `domain/seismicSymbology.ts`.
 */
const SEISMIC_RADIUS: ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['zoom'],
  6,
  [
    'interpolate',
    ['linear'],
    ['get', 'sizing_magnitude'],
    MIN_SIZED_MAGNITUDE,
    3,
    MAX_SIZED_MAGNITUDE,
    13,
  ],
  10,
  [
    'interpolate',
    ['linear'],
    ['get', 'sizing_magnitude'],
    MIN_SIZED_MAGNITUDE,
    5,
    MAX_SIZED_MAGNITUDE,
    26,
  ],
  14,
  [
    'interpolate',
    ['linear'],
    ['get', 'sizing_magnitude'],
    MIN_SIZED_MAGNITUDE,
    8,
    MAX_SIZED_MAGNITUDE,
    40,
  ],
]

const BAND_COLOR = MAGNITUDE_COLOR_EXPRESSION as unknown as ExpressionSpecification

export const seismicRingLayer: SeismicLayer = {
  id: 'seismic-ring',
  type: 'circle',
  paint: {
    'circle-radius': SEISMIC_RADIUS,
    'circle-color': BAND_COLOR,
    // Relleno casi transparente: el círculo tiene que dejar ver el mapa y los
    // incidentes que haya debajo. Un sismo es contexto, no el sujeto del mapa.
    'circle-opacity': 0.12,
    'circle-stroke-color': BAND_COLOR,
    'circle-stroke-width': 2,
    // Las soluciones sin revisar se dibujan más tenues: su magnitud puede
    // corregirse cuando un sismólogo la valide.
    'circle-stroke-opacity': [
      'case',
      ['==', ['get', 'review_status'], 'automatic'],
      0.6,
      0.95,
    ],
  },
}

export const seismicCoreLayer: SeismicLayer = {
  id: 'seismic-core',
  type: 'circle',
  paint: {
    'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 1.5, 14, 3],
    'circle-color': BAND_COLOR,
    'circle-opacity': 0.9,
  },
}

export function seismicSelectedLayer(usgsId: string | null): SeismicLayer {
  return {
    id: 'seismic-selected',
    type: 'circle',
    filter: ['==', ['get', 'usgs_id'], usgsId ?? ' '],
    paint: {
      'circle-radius': ['+', SEISMIC_RADIUS, 4],
      'circle-color': 'transparent',
      'circle-stroke-color': '#0f172a',
      'circle-stroke-width': 2.5,
    },
  }
}

export const seismicHitLayer: SeismicLayer = {
  id: SEISMIC_HIT_LAYER_ID,
  type: 'circle',
  paint: {
    'circle-radius': ['max', ['+', SEISMIC_RADIUS, 6], 14],
    'circle-color': '#000000',
    'circle-opacity': 0,
  },
}
