/**
 * Capas de MapLibre para los incidentes — política de confianza v2.0.0.
 *
 * Orden de dibujo, de abajo hacia arriba:
 *
 *   1. `alert-halo`   — anillo exterior. Eje SENAPRED.
 *   2. `casing`       — aro blanco. Separa el relleno del anillo de alerta.
 *   3. `core`         — disco relleno. Tramo de `confidence_level`.
 *   4. `closed-ring`  — anillo punteado sobre los incidentes ya cerrados.
 *   5. `unverified`   — punto central hueco: `confirmed` sin verificación.
 *   6. `selected`     — realce del incidente abierto en la ficha.
 *   7. `hit`          — círculo invisible y generoso. Sólo existe para el dedo.
 *
 * El `casing` blanco no es decorativo. Desde la v2.0.0 el relleno puede ser rojo
 * (#dc2626) o amarillo (#eab308), y el anillo de alerta de SENAPRED también es
 * rojo (#e11d48) o amarillo (#f59e0b). Sin un aro neutro en medio, un incidente
 * `unsafe` con alerta roja sería una mancha roja uniforme y los dos ejes se
 * volverían ilegibles justo cuando más importan.
 *
 * Ningún color se escribe acá: todos salen de `domain/symbology`, que también
 * alimenta la leyenda y la ficha.
 */

import type { CircleLayerSpecification, ExpressionSpecification } from 'maplibre-gl'
import { ALERT_COLOR_EXPRESSION, LEVEL_COLOR_EXPRESSION } from '@/domain/symbology'

export type IncidentLayer = Omit<CircleLayerSpecification, 'source'>

export const INCIDENT_SOURCE_ID = 'incidents'
export const INCIDENT_HIT_LAYER_ID = 'incidents-hit'

/**
 * Radio del disco. Crece con el zoom y, dentro de cada zoom, los tramos con más
 * evidencia se dibujan más grandes: la jerarquía visual sigue a la informativa,
 * y no al revés. Es lo que impide que el rojo de `unsafe` —que es una
 * advertencia sobre el dato— domine el mapa por encima de un incendio real.
 */
const SIZE_BY_LEVEL: ExpressionSpecification = [
  'match',
  ['get', 'confidence_level'],
  'confirmed',
  1.0,
  'possible',
  0.78,
  'unsafe',
  0.58,
  0.78,
]

const CORE_RADIUS: ExpressionSpecification = [
  '*',
  SIZE_BY_LEVEL,
  ['interpolate', ['linear'], ['zoom'], 6, 7, 10, 13, 14, 20],
]

const HALO_RADIUS: ExpressionSpecification = [
  '+',
  CORE_RADIUS,
  ['interpolate', ['linear'], ['zoom'], 6, 7, 14, 11],
]

/** Sólo los incidentes con alerta oficial vigente llevan anillo. */
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

/** Aro neutro entre el relleno y el anillo de alerta. Ver nota de arriba. */
export const casingLayer: IncidentLayer = {
  id: 'incidents-casing',
  type: 'circle',
  paint: {
    'circle-radius': ['+', CORE_RADIUS, 2.5],
    'circle-color': '#ffffff',
    'circle-opacity': 0.95,
  },
}

export const coreLayer: IncidentLayer = {
  id: 'incidents-core',
  type: 'circle',
  paint: {
    'circle-radius': CORE_RADIUS,
    'circle-color': LEVEL_COLOR_EXPRESSION as unknown as ExpressionSpecification,
    // Los cerrados además pierden opacidad: color atenuado y menos presencia.
    'circle-opacity': ['case', ['get', 'is_closed'], 0.6, 0.95],
  },
}

/**
 * Anillo punteado de los incidentes cerrados.
 *
 * MapLibre no tiene trazo punteado en la capa de círculos, así que el efecto se
 * consigue con un anillo fino de color apagado — suficiente para leer "esto ya
 * no está activo" sin robarle el canal del color al tramo de confianza.
 */
export const closedRingLayer: IncidentLayer = {
  id: 'incidents-closed-ring',
  type: 'circle',
  filter: ['get', 'is_closed'],
  paint: {
    'circle-radius': ['+', CORE_RADIUS, 1],
    'circle-color': 'transparent',
    'circle-stroke-color': '#475569',
    'circle-stroke-width': 1.5,
    'circle-stroke-opacity': 0.85,
  },
}

/**
 * Punto central hueco para los `confirmed` que ninguna fuente verificó en
 * terreno. Es la marca que impide que el naranja se lea como "CONAF confirmó".
 */
export const unverifiedLayer: IncidentLayer = {
  id: 'incidents-unverified',
  type: 'circle',
  filter: ['get', 'unverified_confirmed'],
  paint: {
    'circle-radius': ['*', CORE_RADIUS, 0.34],
    'circle-color': '#ffffff',
    'circle-opacity': 0.92,
  },
}

export function selectedLayer(code: string | null): IncidentLayer {
  return {
    id: 'incidents-selected',
    type: 'circle',
    filter: ['==', ['get', 'code'], code ?? ' '],
    paint: {
      'circle-radius': ['+', CORE_RADIUS, 7],
      'circle-color': 'transparent',
      'circle-stroke-color': '#0f172a',
      'circle-stroke-width': 3,
    },
  }
}

/**
 * Objetivo táctil. Las guías de accesibilidad piden ~44 px y el disco visible
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
