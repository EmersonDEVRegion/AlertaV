/**
 * Capas de MapLibre para los cuarteles de Bomberos.
 *
 * Capa de **contexto**, como los cortes de ruta: no entra en
 * `interactiveLayerIds` y no le roba el toque a una emergencia. Se ancla a
 * `wind-cone-fill` —la única capa propia montada siempre— por el mismo motivo
 * que la lluvia y los cortes (ver `roadClosureLayers.ts`).
 *
 * El punto aparece recién desde el zoom 10: 146 cuarteles en toda la región,
 * vistos de lejos, taparían los incidentes que son el sujeto del mapa. El
 * nombre, desde el zoom 13.
 */

import type {
  CircleLayerSpecification,
  SymbolLayerSpecification,
} from 'maplibre-gl'
import { CUARTEL_PALETTE } from '@/domain/cuarteles'
import type { Theme } from '@/hooks/useTheme'

export const CUARTELES_SOURCE_ID = 'cuarteles'
const CUARTEL_POINT_LAYER_ID = 'cuartel-point'
const CUARTEL_LABEL_LAYER_ID = 'cuartel-label'
export const CUARTELES_LAYER_IDS = [CUARTEL_POINT_LAYER_ID, CUARTEL_LABEL_LAYER_ID] as const
export const CUARTELES_BEFORE_ID = 'wind-cone-fill'

const CUARTEL_MIN_ZOOM = 10
const CUARTEL_LABEL_MIN_ZOOM = 13

type Visibility = 'visible' | 'none'
const vis = (visible: boolean): Visibility => (visible ? 'visible' : 'none')

export function cuartelPointLayer(
  theme: Theme,
  visible: boolean,
): Omit<CircleLayerSpecification, 'source'> {
  const colors = CUARTEL_PALETTE[theme]
  return {
    id: CUARTEL_POINT_LAYER_ID,
    type: 'circle',
    minzoom: CUARTEL_MIN_ZOOM,
    layout: { visibility: vis(visible) },
    paint: {
      // El cuartel del Cuerpo, un poco más grande que el de una compañía.
      'circle-radius': [
        'interpolate',
        ['linear'],
        ['zoom'],
        CUARTEL_MIN_ZOOM,
        ['case', ['==', ['get', 'tipo'], 'cuerpo'], 4, 3],
        15,
        ['case', ['==', ['get', 'tipo'], 'cuerpo'], 8, 6.5],
      ],
      'circle-color': colors.fill,
      'circle-stroke-color': colors.stroke,
      'circle-stroke-width': ['case', ['==', ['get', 'tipo'], 'cuerpo'], 2.5, 2],
      'circle-pitch-alignment': 'map',
    },
  }
}

export function cuartelLabelLayer(
  theme: Theme,
  visible: boolean,
): Omit<SymbolLayerSpecification, 'source'> {
  const colors = CUARTEL_PALETTE[theme]
  return {
    id: CUARTEL_LABEL_LAYER_ID,
    type: 'symbol',
    minzoom: CUARTEL_LABEL_MIN_ZOOM,
    layout: {
      visibility: vis(visible),
      // Sin `text-font`: el defecto de MapLibre lo sirve el estilo base, igual
      // que en `outageLayers.ts`.
      'text-field': ['get', 'nombre'],
      'text-size': 11,
      'text-offset': [0, 1.1],
      'text-anchor': 'top',
      'text-max-width': 10,
      'text-optional': true,
    },
    paint: {
      'text-color': colors.text,
      'text-halo-color': colors.halo,
      'text-halo-width': 1.4,
    },
  }
}
