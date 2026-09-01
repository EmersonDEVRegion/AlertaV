/**
 * Capas de la amenaza sísmica.
 *
 * Tres, y las tres viven en la misma fuente lógica:
 *
 *   1. `seismic-hazard-heat` — mapa de calor sobre los NODOS de la grilla.
 *                              Domina a escala regional.
 *   2. `seismic-hazard-fill` — relleno por celda. Domina al acercarse.
 *   3. `seismic-hazard-line` — retícula tenue, acompaña al relleno.
 *
 * La transición entre (1) y (2) es una interpolación de opacidad sobre el zoom.
 * El porqué está en `domain/hazardSymbology.ts`, junto a `HAZARD_CROSSFADE`.
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
  HeatmapLayerSpecification,
  LineLayerSpecification,
} from 'maplibre-gl'
import {
  HAZARD_CROSSFADE,
  HAZARD_HEAT,
  HAZARD_HEAT_INTENSITY,
  HAZARD_HEAT_RADIUS,
  HAZARD_MAX_G,
  HAZARD_MIN_G,
  HAZARD_RAMP,
  HAZARD_VARIABLE,
} from '@/domain/hazardSymbology'

export const HAZARD_CELL_SOURCE_ID = 'seismic-hazard'
export const HAZARD_NODE_SOURCE_ID = 'seismic-hazard-nodes'
export const HAZARD_HEAT_LAYER_ID = 'seismic-hazard-heat'
export const HAZARD_FILL_LAYER_ID = 'seismic-hazard-fill'
export const HAZARD_LINE_LAYER_ID = 'seismic-hazard-line'

/** En orden de dibujo. Lo usa el re-anclaje tras un cambio de estilo. */
export const HAZARD_LAYER_IDS = [
  HAZARD_HEAT_LAYER_ID,
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

/**
 * Desvanecido cruzado.
 *
 * `peak` es la opacidad en el rango dominante; el otro extremo es 0. Se
 * construye a partir de `HAZARD_CROSSFADE` para que las dos capas usen
 * literalmente los mismos dos números: si alguien mueve la ventana, se mueven
 * juntas o queda un hueco en el que no se ve ninguna de las dos.
 */
function crossfade(peak: number, direction: 'out' | 'in'): ExpressionSpecification {
  const [from, to] = HAZARD_CROSSFADE
  return direction === 'out'
    ? byZoom([
        [from, peak],
        [to, 0],
      ])
    : byZoom([
        [from, 0],
        [to, peak],
      ])
}

/* ------------------------------------------------------------------------- */
/* 1. Mapa de calor                                                           */
/* ------------------------------------------------------------------------- */

/**
 * Peso de cada nodo: el PGA, normalizado al rango de la rampa.
 *
 * `heatmap-weight` espera un número **sin unidad** en el que 1 es «cuenta
 * como un punto entero». Pasar el PGA crudo (0,15–0,6 g) daría pesos por debajo
 * de 1 en todos los nodos y una densidad tan baja que la rampa nunca saldría de
 * su primer tramo — el mapa entero del mismo violeta pálido.
 *
 * El piso es 0,05 y no 0: un nodo en el extremo bajo de la rampa **sí existe** y
 * debe aportar campo. Con peso 0 desaparecería, y un hueco en una grilla
 * regular se lee como «acá no hay amenaza», que es falso y además tranquiliza.
 */
function heatWeight(): ExpressionSpecification {
  return [
    'interpolate',
    ['linear'],
    ['to-number', ['get', 'value'], 0],
    HAZARD_MIN_G,
    0.05,
    HAZARD_MAX_G,
    1,
  ] as ExpressionSpecification
}

/**
 * Rampa de densidad.
 *
 * `["heatmap-density"]` sólo se puede usar acá dentro, y esta expresión no
 * admite `["zoom"]`: el color del mapa de calor no puede depender de la escala.
 * Lo que sí depende del zoom son el radio, la intensidad y la opacidad.
 */
function heatColor(theme: Theme): ExpressionSpecification {
  return [
    'interpolate',
    ['linear'],
    ['heatmap-density'],
    ...HAZARD_HEAT[theme].stops.flatMap(([density, color]) => [density, color]),
  ] as unknown as ExpressionSpecification
}

export function hazardHeatLayer(
  theme: Theme,
  visible: boolean,
): Omit<HeatmapLayerSpecification, 'source'> {
  return {
    id: HAZARD_HEAT_LAYER_ID,
    type: 'heatmap',
    /*
     * Se apaga por `visibility`, nunca desmontando la fuente. Desmontar
     * destruiría la geometría ya subida al worker y volver a encender pagaría
     * la subida otra vez. Con `visibility: 'none'` MapLibre deja de dibujarla.
     */
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'heatmap-weight': heatWeight(),
      'heatmap-intensity': byZoom(HAZARD_HEAT_INTENSITY),
      'heatmap-color': heatColor(theme),
      'heatmap-radius': byZoom(HAZARD_HEAT_RADIUS),
      // Se retira al acercarse: a partir de ahí el dato es la celda.
      'heatmap-opacity': crossfade(HAZARD_HEAT[theme].opacity, 'out'),
    },
  }
}

/* ------------------------------------------------------------------------- */
/* 2 y 3. Celdas del modelo                                                   */
/* ------------------------------------------------------------------------- */

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
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'fill-color': fillColor(theme),
      // Entra al acercarse, cuando el mapa de calor se retira.
      'fill-opacity': crossfade(HAZARD_RAMP[theme].fillOpacity, 'in'),
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
      'line-opacity': crossfade(HAZARD_RAMP[theme].lineOpacity, 'in'),
    },
  }
}
