/**
 * Capas de MapLibre para las zonas de la DMC con avisos vigentes.
 *
 * Cuatro capas, de abajo hacia arriba:
 *
 *   1. `meteo-zone-fill`  — relleno muy tenue, por severidad.
 *   2. `meteo-zone-line`  — contorno punteado de los Avisos.
 *   3. `meteo-zone-strong`— contorno continuo de Alertas y Alarmas, más grueso.
 *                           Dos capas porque `line-dasharray` no admite
 *                           expresiones por feature.
 *   4. `meteo-zone-focus` — contorno grueso sólo en las zonas del aviso que
 *                           pidió «Ver en el mapa».
 *
 * # Contexto, no emergencia
 *
 * Una zona de pronóstico mide decenas de kilómetros: «Litoral» va de Papudo a
 * Santo Domingo. No es el lugar de un hecho y no abre ficha: no entra en
 * `interactiveLayerIds`. El detalle está en la franja de «Ahora».
 *
 * # Orden de dibujo
 *
 * Mismo ancla que las demás capas de contexto, `wind-cone-fill`: es la única
 * capa propia montada siempre. Ver `roadClosureLayers.ts`.
 */

import type {
  ExpressionSpecification,
  FillLayerSpecification,
  LineLayerSpecification,
} from 'maplibre-gl'
import { METEO_ZONE_FILL_OPACITY, METEO_ZONE_PALETTE } from '@/domain/meteoWarnings'
import type { Theme } from '@/hooks/useTheme'

export const METEO_ZONES_SOURCE_ID = 'meteo-zones'
const METEO_ZONE_FILL_LAYER_ID = 'meteo-zone-fill'
const METEO_ZONE_LINE_LAYER_ID = 'meteo-zone-line'
const METEO_ZONE_STRONG_LAYER_ID = 'meteo-zone-strong'
export const METEO_ZONE_FOCUS_LAYER_ID = 'meteo-zone-focus'
export const METEO_ZONES_BEFORE_ID = 'wind-cone-fill'

export const METEO_ZONE_LAYER_IDS = [
  METEO_ZONE_FILL_LAYER_ID,
  METEO_ZONE_LINE_LAYER_ID,
  METEO_ZONE_STRONG_LAYER_ID,
  METEO_ZONE_FOCUS_LAYER_ID,
] as const

type Fill = Omit<FillLayerSpecification, 'source'>
type Line = Omit<LineLayerSpecification, 'source'>

function bySeverity<T>(values: Record<1 | 2 | 3, T>): ExpressionSpecification {
  return [
    'match',
    ['get', 'severidad'],
    3,
    values[3] as never,
    2,
    values[2] as never,
    values[1] as never,
  ] as ExpressionSpecification
}

/**
 * ¿Esta zona es del aviso `id`? `ids` es texto separado por comas: se compara
 * con comas a los dos lados para que `58` no calce dentro de `5852`.
 */
export function focusFilter(id: string | null): ExpressionSpecification {
  if (!id) return ['==', 1, 0]
  return ['in', `,${id},`, ['concat', ',', ['get', 'ids'], ',']]
}

export function meteoZoneSpecs(
  theme: Theme,
  visible: boolean,
  focusId: string | null,
): { fill: Fill; line: Line; strong: Line; focus: Line } {
  const visibility = visible ? 'visible' : 'none'
  const color = bySeverity(METEO_ZONE_PALETTE[theme])
  return {
    fill: {
      id: METEO_ZONE_FILL_LAYER_ID,
      type: 'fill',
      layout: { visibility },
      paint: { 'fill-color': color, 'fill-opacity': bySeverity(METEO_ZONE_FILL_OPACITY) },
    },
    line: {
      id: METEO_ZONE_LINE_LAYER_ID,
      type: 'line',
      filter: ['==', ['get', 'severidad'], 1],
      layout: { visibility, 'line-join': 'round' },
      paint: { 'line-color': color, 'line-opacity': 0.75, 'line-width': 1.25, 'line-dasharray': [3, 2] },
    },
    strong: {
      id: METEO_ZONE_STRONG_LAYER_ID,
      type: 'line',
      filter: ['>=', ['get', 'severidad'], 2],
      layout: { visibility, 'line-join': 'round' },
      paint: {
        'line-color': color,
        'line-opacity': 0.85,
        'line-width': bySeverity({ 1: 1.25, 2: 2, 3: 2.75 }),
      },
    },
    focus: {
      id: METEO_ZONE_FOCUS_LAYER_ID,
      type: 'line',
      filter: focusFilter(focusId),
      layout: { visibility, 'line-join': 'round' },
      paint: { 'line-color': color, 'line-width': 3.5, 'line-opacity': 0.95 },
    },
  }
}
