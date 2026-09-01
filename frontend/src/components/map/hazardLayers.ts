/**
 * Capas de la amenaza sísmica.
 *
 * Dos, y las dos sobre la MISMA fuente de polígonos:
 *
 *   1. `seismic-hazard-fill` — la superficie de intensidad. Es la capa.
 *   2. `seismic-hazard-line` — retícula de celda, sólo al acercarse mucho.
 *
 * # Qué se fue de acá: el mapa de calor
 *
 * Había una tercera capa, `seismic-hazard-heat`, de tipo `heatmap` sobre una
 * segunda fuente de puntos derivada de los centros de celda. Dominaba a escala
 * regional y se relevaba con el relleno al hacer zoom. Se retiró entera, con su
 * fuente. Los dos motivos están largos en `domain/hazardSymbology.ts`; en corto:
 *
 *   - un `heatmap` es un estimador de densidad, y sobre una grilla **regular**
 *     la densidad es constante: lo único que podía hacer el kernel era sumar el
 *     valor de los vecinos, que no es el PGA de ningún punto;
 *   - las celdas no teselaban por un bug del generador del artefacto, así que
 *     el relleno no podía sostener la escala regional y el `heatmap` estaba
 *     tapando ese agujero. Arreglado el generador, sobra.
 *
 * El resultado es una capa sola, continua en todo el rango de zoom, con la
 * misma gramática que los mapas oficiales del CSN.
 *
 * # Jerarquía visual: por qué `beforeId` y no un z-index
 *
 * MapLibre no tiene z-index. El orden de dibujo es el orden del arreglo de
 * capas del estilo, y la única forma de controlarlo es decir **antes de qué
 * capa** se inserta cada una.
 *
 * # El anclaje: un bug latente que sí estaba
 *
 * `HAZARD_BEFORE_ID` apuntaba a `seismic-reach-fill`, que es **condicional**:
 * sólo existe mientras la casilla de sismos esté encendida. MapLibre no ignora
 * un `beforeId` inexistente — emite un `error` y **descarta la capa entera**.
 * O sea: apagar los sismos y encender después la amenaza daba una capa que no
 * aparecía nunca, sin nada en pantalla que lo explicara.
 *
 * Ahora apunta a `wind-cone-fill`, la única capa propia que está montada
 * siempre. Es el mismo ancla que ya usaba la lluvia y por la misma razón; hay
 * un test que ata la constante al `id` real.
 *
 * Queda: amenaza → lluvia → radio sísmico → cono → sismos → incidentes, y los
 * pines de cortes por encima de todo por ser marcadores del DOM.
 */

import type {
  ExpressionSpecification,
  FillLayerSpecification,
  LineLayerSpecification,
} from 'maplibre-gl'
import {
  HAZARD_FILL_OPACITY,
  HAZARD_LINE_OPACITY,
  HAZARD_RAMP,
  HAZARD_RETICULE,
  HAZARD_VARIABLE,
} from '@/domain/hazardSymbology'

export const HAZARD_CELL_SOURCE_ID = 'seismic-hazard'
export const HAZARD_FILL_LAYER_ID = 'seismic-hazard-fill'
export const HAZARD_LINE_LAYER_ID = 'seismic-hazard-line'

/** En orden de dibujo. Lo usa el re-anclaje tras un cambio de estilo. */
export const HAZARD_LAYER_IDS = [
  HAZARD_FILL_LAYER_ID,
  HAZARD_LINE_LAYER_ID,
] as const

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

/* ------------------------------------------------------------------------- */
/* 1. La superficie de intensidad                                             */
/* ------------------------------------------------------------------------- */

/**
 * Color por valor de PGA.
 *
 * # `interpolate-lab` y no `interpolate` a secas
 *
 * El interpolador por defecto mezcla en sRGB, que no es perceptualmente
 * uniforme: entre dos violetas separados el punto medio sale más apagado que
 * cualquiera de los dos, y sobre una teselación de celdas contiguas ese hundido
 * se ve como una banda que no está en el dato. En CIELAB la mezcla avanza en
 * pasos que el ojo lee como iguales, que es justo lo que pide una superficie
 * que se quiere leer como continua.
 *
 * No se usa `interpolate-hcl` porque interpola el tono por el camino corto del
 * círculo cromático: bastaría que alguien metiera una parada un poco azulada
 * para que la rampa cruzara por cianes que esta capa tiene prohibidos —son de
 * la lluvia—. `lab` no rota tono, sólo recorre el segmento entre los colores
 * declarados.
 *
 * MapLibre satura fuera de rango —un valor bajo el mínimo toma el primer color
 * y uno sobre el máximo el último—, que es el comportamiento deseado: nada
 * desaparece del mapa por caer fuera de la rampa.
 */
function fillColor(theme: Theme): ExpressionSpecification {
  return [
    'interpolate-lab',
    ['linear'],
    // `to-number` con respaldo: una celda sin la variable no revienta la
    // expresión, se dibuja en el extremo bajo.
    ['to-number', ['get', HAZARD_VARIABLE], 0],
    ...HAZARD_RAMP[theme].stops.flatMap(([value, color]) => [value, color]),
  ] as unknown as ExpressionSpecification
}

export function hazardFillLayer(
  theme: Theme,
  visible: boolean,
): Omit<FillLayerSpecification, 'source'> {
  return {
    id: HAZARD_FILL_LAYER_ID,
    type: 'fill',
    /*
     * Se apaga por `visibility`, nunca desmontando la fuente. Desmontar
     * destruiría la geometría ya subida al worker y volver a encender pagaría
     * la subida otra vez. Con `visibility: 'none'` MapLibre deja de dibujarla.
     */
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'fill-color': fillColor(theme),
      'fill-opacity': byZoom(HAZARD_FILL_OPACITY[theme]),
      /*
       * `fill-antialias: false` en una teselación de celdas contiguas.
       *
       * Con antialias, los bordes compartidos entre celdas vecinas se mezclan
       * dos veces y aparece una retícula de costuras claras que no está en el
       * dato — el mismo artefacto que esta reescritura vino a quitar, pero por
       * otro camino. Apagarlo también ahorra trabajo de rasterizado en una capa
       * que puede tener miles de polígonos.
       */
      'fill-antialias': false,
    },
  }
}

/* ------------------------------------------------------------------------- */
/* 2. La retícula                                                             */
/* ------------------------------------------------------------------------- */

/**
 * Borde de celda, sólo de cerca.
 *
 * Antes entraba en la misma ventana en la que se retiraba el mapa de calor
 * (z10,5–12,2), o sea mientras las celdas todavía median pocos píxeles: dibujar
 * un trazo alrededor de cada rectángulo de 20 px es exactamente cómo una
 * superficie continua se convierte en una cuadrícula. Ahora espera a que una
 * celda ocupe buena parte de la pantalla, y ahí sí dice algo: que el modelo se
 * resuelve cada 5 km y que el degradado de más lejos era interpolación.
 */
export function hazardLineLayer(
  theme: Theme,
  visible: boolean,
): Omit<LineLayerSpecification, 'source'> {
  const [hidden, shown] = HAZARD_RETICULE
  return {
    id: HAZARD_LINE_LAYER_ID,
    type: 'line',
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'line-color': HAZARD_RAMP[theme].line,
      // Fino y casi transparente: sugiere la grilla sin dibujar una reja.
      'line-width': 0.5,
      'line-opacity': byZoom([
        [hidden, 0],
        [shown, HAZARD_LINE_OPACITY],
      ]),
    },
  }
}
