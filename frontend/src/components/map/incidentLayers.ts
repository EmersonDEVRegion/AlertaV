/**
 * Capas de MapLibre para los incidentes.
 *
 * El orden importa y es el que se declara aquí:
 *
 *   1. `alert-halo`  — anillo exterior. Eje "que declaro SENAPRED".
 *   2. `core`        — disco relleno. Eje "cuan seguros estamos del fenómeno".
 *   3. `selected`    — realce del incidente abierto en la tarjeta.
 *   4. `hit`         — círculo invisible y generoso. Solo existe para el dedo.
 *
 * Ningun color se escribe aquí: todos salen de `domain/symbology`, que también
 * alimenta la leyenda y la tarjeta. Es la única forma de garantizar que lo que
 * el mapa pinta y lo que la leyenda promete no se separen con el tiempo.
 */

import type { CircleLayerSpecification, ExpressionSpecification } from 'maplibre-gl'
import {
  ALERT_COLOR_EXPRESSION,
  PHENOMENON_COLOR_EXPRESSION,
} from '@/domain/symbology'

export type IncidentLayer = Omit<CircleLayerSpecification, 'source'>

export const INCIDENT_SOURCE_ID = 'incidents'
export const INCIDENT_HIT_LAYER_ID = 'incidents-hit'

/**
 * Radio del disco. Crece con el zoom y, dentro de cada zoom, lo confirmado se
 * dibuja más grande: la jerarquía visual debe coincidir con la jerarquía
 * informativa.
 */
const CORE_RADIUS: ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['zoom'],
  6,
  ['case', ['get', 'is_official_confirmed'], 6, 4],
  10,
  ['case', ['get', 'is_official_confirmed'], 11, 8],
  14,
  ['case', ['get', 'is_official_confirmed'], 18, 13],
]

const HALO_RADIUS: ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['zoom'],
  6,
  12,
  10,
  19,
  14,
  27,
]

/** Solo los incidentes con alerta oficial vigente llevan anillo. */
const HAS_ALERT: ExpressionSpecification = ['!=', ['get', 'alert_level'], '']

export const alertHaloLayer: IncidentLayer = {
  id: 'incidents-alert-halo',
  type: 'circle',
  filter: HAS_ALERT,
  paint: {
    'circle-radius': HALO_RADIUS,
    'circle-color': ALERT_COLOR_EXPRESSION as unknown as ExpressionSpecification,
    'circle-opacity': 0.16,
    'circle-stroke-color': ALERT_COLOR_EXPRESSION as unknown as ExpressionSpecification,
    'circle-stroke-width': 2,
    'circle-stroke-opacity': 0.9,
  },
}

export const coreLayer: IncidentLayer = {
  id: 'incidents-core',
  type: 'circle',
  paint: {
    'circle-radius': CORE_RADIUS,
    'circle-color': PHENOMENON_COLOR_EXPRESSION as unknown as ExpressionSpecification,
    // La opacidad sigue a la confianza, pero con piso: nada baja tanto como
    // para volverse invisible. Un incidente que existe debe poder verse.
    'circle-opacity': [
      'interpolate',
      ['linear'],
      ['get', 'confidence'],
      0,
      0.55,
      1,
      0.95,
    ],
    'circle-stroke-color': '#ffffff',
    'circle-stroke-width': 1.5,
  },
}

export function selectedLayer(code: string | null): IncidentLayer {
  return {
    id: 'incidents-selected',
    type: 'circle',
    filter: ['==', ['get', 'code'], code ?? ' '],
    paint: {
      'circle-radius': ['+', CORE_RADIUS, 6],
      'circle-color': 'transparent',
      'circle-stroke-color': '#0f172a',
      'circle-stroke-width': 3,
    },
  }
}

/**
 * Objetivo táctil. Las guias de accesibilidad piden ~44 px y el disco visible
 * mide menos que eso en zooms bajos, así que la superficie que recibe el toque
 * se separa de la que se ve.
 */
export const hitLayer: IncidentLayer = {
  id: INCIDENT_HIT_LAYER_ID,
  type: 'circle',
  paint: {
    'circle-radius': ['+', CORE_RADIUS, 12],
    'circle-color': '#000000',
    'circle-opacity': 0,
  },
}
