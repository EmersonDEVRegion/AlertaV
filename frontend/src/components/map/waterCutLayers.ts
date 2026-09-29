/**
 * Cortes de agua de Esval sobre el lienzo.
 *
 * El mismo modelo que los cortes de luz (`outageLayers.ts`) —un disco con un
 * glifo SDF encima— con tres diferencias deliberadas:
 *
 * - **Sin agrupar.** Un mal día son unos treinta cortes en toda la región: un
 *   racimo escondería el de tu calle sin ahorrar nada.
 * - **Por debajo de todo lo demás.** Un corte de agua es contexto, no un
 *   siniestro: un incendio o un corte de luz en el mismo punto lo tapan, y
 *   también le ganan el toque (ver `handleClick` en `IncidentMap`).
 * - **Se apaga con `visibility`.** La fuente está siempre montada; apagar la
 *   fila del panel cambia una propiedad de diseño, sin `setData` ni remontaje.
 *
 * Dentro de la capa, los de emergencia se dibujan encima de los programados.
 */

import type {
  CircleLayerSpecification,
  ExpressionSpecification,
  FilterSpecification,
  SymbolLayerSpecification,
} from 'maplibre-gl'
import { WATER_ICON } from '@/domain/emergencyIcons'
import { WATER } from '@/domain/waterSymbology'
import type { Theme } from '@/hooks/useTheme'
import { PIN_EDGE, PIN_INK, pinRadius } from './outageLayers'

type CircleLayer = Omit<CircleLayerSpecification, 'source'>
type SymbolLayer = Omit<SymbolLayerSpecification, 'source'>
type Visibility = 'visible' | 'none'

export const WATER_SOURCE_ID = 'water-cuts'
export const WATER_HIT_LAYER_ID = 'water-cuts-hit'
export const WATER_SELECTED_LAYER_ID = 'water-cuts-selected'

/** Emergencia encima de programado: `emergencia` es 1 o 0 (ver `toWaterCutFeatureCollection`). */
const BY_URGENCY: ExpressionSpecification = ['get', 'emergencia']

const visibility = (visible: boolean): Visibility => (visible ? 'visible' : 'none')

export function waterPinLayer(theme: Theme, visible: boolean): CircleLayer {
  return {
    id: 'water-cuts-pin',
    type: 'circle',
    layout: { visibility: visibility(visible), 'circle-sort-key': BY_URGENCY },
    paint: {
      'circle-color': WATER.color,
      'circle-radius': pinRadius(),
      'circle-stroke-color': PIN_EDGE[theme],
      'circle-stroke-width': 2,
    },
  }
}

/** La gota dentro del disco. Sólo se monta cuando los iconos ya están registrados. */
export function waterGlyphLayer(visible: boolean): SymbolLayer {
  return {
    id: 'water-cuts-glyph',
    type: 'symbol',
    layout: {
      visibility: visibility(visible),
      'icon-image': WATER_ICON,
      'icon-size': ['interpolate', ['linear'], ['zoom'], 7, 0.2, 11, 0.26, 15, 0.32],
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
      'symbol-sort-key': BY_URGENCY,
    },
    paint: { 'icon-color': WATER.onColor },
  }
}

export function waterSelectedLayer(theme: Theme, id: string | null, visible: boolean): CircleLayer {
  return {
    id: WATER_SELECTED_LAYER_ID,
    type: 'circle',
    // Un id que ninguna feature tiene cuando no hay selección: el filtro no
    // puede comparar contra `null`.
    filter: ['==', ['get', 'water_id'], id ?? ' '] as FilterSpecification,
    layout: { visibility: visibility(visible) },
    paint: {
      'circle-radius': pinRadius(5),
      'circle-color': 'transparent',
      'circle-stroke-color': PIN_INK[theme],
      'circle-stroke-width': 2.5,
    },
  }
}

/** Objetivo táctil: más grande que el disco, invisible. */
export function waterHitLayer(visible: boolean): CircleLayer {
  return {
    id: WATER_HIT_LAYER_ID,
    type: 'circle',
    layout: { visibility: visibility(visible) },
    paint: {
      'circle-radius': pinRadius(10),
      'circle-color': '#000000',
      'circle-opacity': 0,
    },
  }
}
