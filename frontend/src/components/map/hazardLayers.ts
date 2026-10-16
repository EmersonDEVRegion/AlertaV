/**
 * Capa de la amenaza sísmica.
 *
 * Una sola capa `raster` sobre una fuente `image`: la superficie de intensidad
 * ya interpolada y con el borde difuminado (`lib/hazardRaster.ts`).
 *
 * # Por qué una imagen
 *
 * - **No un mapa de calor:** un `heatmap` sobre una grilla regular mide
 *   densidad, que es constante, así que el kernel sólo suma el valor de los
 *   vecinos. No es el PGA de ningún punto (ver `domain/hazardSymbology.ts`).
 * - **No polígonos por celda:** 1648 rectángulos de un solo color dibujan la
 *   caja de la grilla con bordes rectos y, de cerca, escalones de 5 km. La
 *   imagen interpola entre nodos, desvanece el borde y cuesta menos en el
 *   teléfono. La resolución la dice la leyenda.
 *
 * # Jerarquía visual: por qué `beforeId` y no un z-index
 *
 * MapLibre no tiene z-index. El orden de dibujo es el orden del arreglo de
 * capas del estilo, y la única forma de controlarlo es decir **antes de qué
 * capa** se inserta cada una.
 *
 * # El anclaje: tiene que ser una capa incondicional
 *
 * `HAZARD_BEFORE_ID` apuntaba a `seismic-reach-fill`, que sólo existe mientras
 * la casilla de sismos esté encendida. MapLibre no ignora un `beforeId`
 * inexistente — emite un `error` y **descarta la capa entera**. Ahora apunta a
 * `wind-cone-fill`, la única capa propia que está montada siempre; hay un test
 * que ata la constante al `id` real.
 *
 * Queda: amenaza → lluvia → radio sísmico → cono → sismos → incidentes.
 */

import type { ExpressionSpecification, RasterLayerSpecification } from 'maplibre-gl'
import { HAZARD_FILL_OPACITY } from '@/domain/hazardSymbology'

export const HAZARD_SOURCE_ID = 'seismic-hazard'
export const HAZARD_FIELD_LAYER_ID = 'seismic-hazard-field'

/** En orden de dibujo. Lo usa el re-anclaje tras un cambio de estilo. */
export const HAZARD_LAYER_IDS = [HAZARD_FIELD_LAYER_ID] as const

/**
 * Ancla del orden de dibujo. **Tiene que ser una capa INCONDICIONAL.**
 *
 * Ver la nota de la cabecera: un ancla que se desmonta con otra casilla hace
 * que MapLibre descarte esta capa en silencio.
 */
export const HAZARD_BEFORE_ID = 'wind-cone-fill'

type Theme = 'light' | 'dark'

/** Escribe una interpolación con `["zoom"]` como raíz. Ver nota en `rainLayers.ts`. */
function byZoom(
  stops: readonly (readonly [number, number])[],
): ExpressionSpecification {
  return [
    'interpolate',
    ['linear'],
    ['zoom'],
    ...stops.flatMap(([zoom, value]) => [zoom, value]),
  ] as unknown as ExpressionSpecification
}

/**
 * La superficie de intensidad.
 *
 * El color viene en la imagen (la rampa en CIELAB, igual que el
 * `interpolate-lab` del relleno anterior). Acá sólo se decide la opacidad por
 * zoom —la misma envolvente que tenía el relleno: pesa a escala regional y se
 * aligera al acercarse— y el suavizado.
 */
export function hazardFieldLayer(
  theme: Theme,
  visible: boolean,
): Omit<RasterLayerSpecification, 'source'> {
  return {
    id: HAZARD_FIELD_LAYER_ID,
    type: 'raster',
    /*
     * Se apaga por `visibility`, nunca desmontando la fuente. Desmontar
     * destruiría la imagen ya subida y volver a encender pagaría la subida
     * otra vez.
     */
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'raster-opacity': byZoom(HAZARD_FILL_OPACITY[theme]),
      // Al acercarse, un píxel de 600 m no debe verse como un bloque.
      'raster-resampling': 'linear',
      // Al cambiar de tema cambia la imagen entera: sin fundido.
      'raster-fade-duration': 0,
    },
  }
}
