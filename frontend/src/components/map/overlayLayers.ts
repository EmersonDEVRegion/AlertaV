/**
 * Capas de los polígonos derivados.
 *
 * Van **por debajo** de todo lo demás en el orden de dibujo: son contexto
 * calculado y no deben tapar ni la señal ni el incidente. Ambas usan relleno
 * muy tenue y un borde punteado, un lenguaje visual distinto del de los
 * marcadores, para que se lean como estimaciones y no como hechos medidos.
 */

import type {
  FillLayerSpecification,
  LineLayerSpecification,
} from 'maplibre-gl'

export type OverlayFill = Omit<FillLayerSpecification, 'source'>
export type OverlayLine = Omit<LineLayerSpecification, 'source'>

export const REACH_SOURCE_ID = 'seismic-reach'
export const CONE_SOURCE_ID = 'wind-cone'

/** Radio de percepción: gris azulado neutro, sin relación con la escala de magnitud. */
export const reachFillLayer: OverlayFill = {
  id: 'seismic-reach-fill',
  type: 'fill',
  paint: {
    'fill-color': '#38bdf8',
    'fill-opacity': 0.07,
  },
}

export const reachLineLayer: OverlayLine = {
  id: 'seismic-reach-line',
  type: 'line',
  paint: {
    'line-color': '#0ea5e9',
    'line-width': 1,
    'line-opacity': 0.5,
    'line-dasharray': [3, 3],
  },
}

/**
 * Cono de viento: naranja, la familia de los incendios, porque describe hacia
 * dónde puede avanzar uno. Degradado hacia la punta no es posible en una capa
 * `fill`, así que el borde punteado hace el trabajo de comunicar incertidumbre.
 */
export const coneFillLayer: OverlayFill = {
  id: 'wind-cone-fill',
  type: 'fill',
  paint: {
    'fill-color': '#f97316',
    'fill-opacity': 0.18,
  },
}

export const coneLineLayer: OverlayLine = {
  id: 'wind-cone-line',
  type: 'line',
  paint: {
    'line-color': '#ea580c',
    'line-width': 1.5,
    'line-opacity': 0.8,
    'line-dasharray': [2, 2],
  },
}
