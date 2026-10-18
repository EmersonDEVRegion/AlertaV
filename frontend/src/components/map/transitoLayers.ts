/**
 * Capas de MapLibre para el tránsito del MTT.
 *
 * De abajo hacia arriba:
 *
 *   1. `transito-casing` — borde claro (u oscuro) bajo cada tramo, para que la
 *                          línea se lea sobre cualquier calle del mapa base.
 *   2. `transito-focus`  — halo ancho sólo en el tramo que pidió «Ver en el mapa».
 *   3. `transito-line`   — el tramo, con el color de su subtipo.
 *   4. `transito-point`  — un disco cuando el MTT nombró sólo un lugar.
 *
 * # Contexto, no emergencia
 *
 * No entran en `interactiveLayerIds`: un taco no le roba el toque al choque que
 * lo causó. El detalle está en la lista de «Ahora».
 *
 * # Orden de dibujo
 *
 * El mismo ancla que las demás capas de contexto (`wind-cone-fill`, la única
 * propia montada siempre). Ver `roadClosureLayers.ts`.
 */

import type {
  CircleLayerSpecification,
  ExpressionSpecification,
  FilterSpecification,
  LineLayerSpecification,
} from 'maplibre-gl'
import { TRANSITO_SUBTIPOS, type TransitoSubtipo } from '@/api/transito'
import { TRANSITO_PALETTE } from '@/domain/transitoSymbology'
import type { Theme } from '@/hooks/useTheme'

export const TRANSITO_SOURCE_ID = 'transito'
const TRANSITO_CASING_LAYER_ID = 'transito-casing'
const TRANSITO_FOCUS_LAYER_ID = 'transito-focus'
const TRANSITO_LINE_LAYER_ID = 'transito-line'
const TRANSITO_POINT_LAYER_ID = 'transito-point'
export const TRANSITO_BEFORE_ID = 'wind-cone-fill'

export const TRANSITO_LAYER_IDS = [
  TRANSITO_CASING_LAYER_ID,
  TRANSITO_FOCUS_LAYER_ID,
  TRANSITO_LINE_LAYER_ID,
  TRANSITO_POINT_LAYER_ID,
] as const

type Line = Omit<LineLayerSpecification, 'source'>
type Circle = Omit<CircleLayerSpecification, 'source'>

function porSubtipo(theme: Theme): ExpressionSpecification {
  const colores = TRANSITO_PALETTE[theme]
  const pares = TRANSITO_SUBTIPOS.filter((s) => s !== 'congestion').flatMap((s) => [s, colores[s]])
  return ['match', ['get', 'subtipo'], ...pares, colores.congestion] as unknown as ExpressionSpecification
}

/** Sólo los subtipos que no se ocultaron con sus chips. */
export function subtiposFilter(ocultos: readonly TransitoSubtipo[]): ExpressionSpecification {
  const visibles = TRANSITO_SUBTIPOS.filter((s) => !ocultos.includes(s))
  return ['in', ['get', 'subtipo'], ['literal', visibles]]
}

const ES_LINEA: ExpressionSpecification = [
  'in',
  ['geometry-type'],
  ['literal', ['LineString', 'MultiLineString']],
]
const ES_PUNTO: ExpressionSpecification = ['==', ['geometry-type'], 'Point']

/** Grosor según el zoom: fino al ver la región, de una calzada al acercarse. */
const ANCHO: ExpressionSpecification = ['interpolate', ['linear'], ['zoom'], 10, 2, 14, 4, 17, 7]
const ANCHO_BORDE: ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['zoom'],
  10,
  3.5,
  14,
  6.5,
  17,
  10,
]
const ANCHO_FOCO: ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['zoom'],
  10,
  8,
  14,
  14,
  17,
  20,
]

export function transitoSpecs(
  theme: Theme,
  visible: boolean,
  ocultos: readonly TransitoSubtipo[],
  focusId: string | null,
): { casing: Line; focus: Line; line: Line; point: Circle } {
  const visibility = visible ? 'visible' : 'none'
  const color = porSubtipo(theme)
  const borde = theme === 'dark' ? '#0b0f14' : '#ffffff'
  const subtipos = subtiposFilter(ocultos)
  const lineas = ['all', ES_LINEA, subtipos] as FilterSpecification
  const foco = (
    focusId ? ['all', subtipos, ['==', ['get', 'public_id'], focusId]] : ['==', 1, 0]
  ) as FilterSpecification
  return {
    casing: {
      id: TRANSITO_CASING_LAYER_ID,
      type: 'line',
      filter: lineas,
      layout: { visibility, 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': borde, 'line-width': ANCHO_BORDE, 'line-opacity': 0.9 },
    },
    focus: {
      id: TRANSITO_FOCUS_LAYER_ID,
      type: 'line',
      filter: foco,
      layout: { visibility, 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': color, 'line-width': ANCHO_FOCO, 'line-opacity': 0.3 },
    },
    line: {
      id: TRANSITO_LINE_LAYER_ID,
      type: 'line',
      filter: lineas,
      layout: { visibility, 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': color,
        'line-width': ANCHO,
        // El verde confirma, no alarma: un poco más tenue que el resto.
        'line-opacity': ['match', ['get', 'subtipo'], 'normalidad', 0.75, 0.95],
      },
    },
    point: {
      id: TRANSITO_POINT_LAYER_ID,
      type: 'circle',
      filter: ['all', ES_PUNTO, subtipos] as FilterSpecification,
      layout: { visibility },
      paint: {
        'circle-color': color,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3.5, 15, 6.5],
        'circle-stroke-color': borde,
        'circle-stroke-width': 2,
      },
    },
  }
}
