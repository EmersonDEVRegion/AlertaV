/**
 * Capas de la amenaza sísmica.
 *
 * # Jerarquía visual: por qué `beforeId` y no un z-index
 *
 * MapLibre no tiene z-index. El orden de dibujo es el orden del arreglo de
 * capas del estilo, y la única forma de controlarlo es decir **antes de qué
 * capa** se inserta cada una. Hasta ahora este mapa dependía del orden en que
 * aparecen los `<Source>` en el JSX, que funciona pero es frágil: basta que
 * alguien mueva un bloque para que la amenaza tape los incendios.
 *
 * Por eso estas dos capas se anclan explícitamente con `beforeId` a la primera
 * capa de emergencia que se dibuja. Queda garantizado por construcción, no por
 * el orden de lectura de un archivo.
 *
 * Los pines de cortes son un caso aparte y más simple: son elementos del DOM
 * (`<Marker>`), no capas del lienzo WebGL. El navegador los pinta siempre por
 * encima del `<canvas>`, así que ninguna capa puede taparlos por definición.
 */

import type {
  ExpressionSpecification,
  FillLayerSpecification,
  LineLayerSpecification,
} from 'maplibre-gl'
import { HAZARD_RAMP, HAZARD_VARIABLE } from '@/domain/hazardSymbology'

export const HAZARD_SOURCE_ID = 'seismic-hazard'
export const HAZARD_FILL_LAYER_ID = 'seismic-hazard-fill'
export const HAZARD_LINE_LAYER_ID = 'seismic-hazard-line'

/**
 * Ancla del orden de dibujo: la primera capa de emergencia del mapa.
 *
 * Si esta constante deja de coincidir con una capa existente, MapLibre ignora
 * el `beforeId` en silencio y la amenaza pasa a dibujarse ENCIMA de todo. Hay
 * un test que ata este identificador al de `overlayLayers.ts`.
 */
export const HAZARD_BEFORE_ID = 'seismic-reach-fill'

type Theme = 'light' | 'dark'

/**
 * Color por valor de PGA.
 *
 * `interpolate` sobre la propiedad numérica. MapLibre satura fuera de rango —un
 * valor bajo el mínimo toma el primer color y uno sobre el máximo el último—,
 * que es exactamente el comportamiento deseado: nada desaparece del mapa por
 * caer fuera de la rampa.
 */
function fillColor(theme: Theme): ExpressionSpecification {
  return [
    'interpolate',
    ['linear'],
    // `to-number` con respaldo: una celda sin la variable no revienta la
    // expresión, se dibuja en el extremo bajo.
    ['to-number', ['get', HAZARD_VARIABLE], 0],
    ...HAZARD_RAMP[theme].stops.flatMap(([value, color]) => [value, color]),
  ] as ExpressionSpecification
}

export function hazardFillLayer(
  theme: Theme,
  visible: boolean,
): Omit<FillLayerSpecification, 'source'> {
  return {
    id: HAZARD_FILL_LAYER_ID,
    type: 'fill',
    /*
     * Apagar por `visibility` y no desmontando el `<Source>`.
     *
     * Desmontar destruiría la fuente y con ella la geometría ya descargada y
     * parseada; volver a encender la capa dispararía otra descarga. Con
     * `visibility: 'none'` MapLibre simplemente deja de dibujarla: costo cero
     * al alternar, y el archivo se pide UNA vez en toda la sesión.
     */
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'fill-color': fillColor(theme),
      'fill-opacity': HAZARD_RAMP[theme].fillOpacity,
      /*
       * `fill-antialias: false` en una teselación de celdas contiguas.
       *
       * Con antialias, los bordes compartidos entre celdas vecinas se mezclan
       * dos veces y aparece una retícula de costuras claras que no está en el
       * dato. Apagarlo también ahorra trabajo de rasterizado en una capa que
       * puede tener miles de polígonos.
       */
      'fill-antialias': false,
    },
  }
}

export function hazardLineLayer(
  theme: Theme,
  visible: boolean,
): Omit<LineLayerSpecification, 'source'> {
  return {
    id: HAZARD_LINE_LAYER_ID,
    type: 'line',
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'line-color': HAZARD_RAMP[theme].line,
      // Fino y casi transparente: sugiere la grilla sin dibujar una reja.
      'line-width': 0.5,
      'line-opacity': HAZARD_RAMP[theme].lineOpacity,
    },
  }
}
