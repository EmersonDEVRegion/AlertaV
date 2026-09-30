/**
 * Capas de MapLibre para la lluvia pronosticada.
 *
 * Tres, de abajo hacia arriba:
 *
 *   1. `rain-field`       — el campo: la grilla del modelo interpolada como
 *                           imagen (`lib/rainRaster.ts`), en todos los zooms.
 *   2. `rain-risk-label`  — «Viña del Mar · riesgo de anegamiento» sobre las
 *                           comunas con el flag, a escala regional.
 *   3. `rain-text`        — el pronóstico en letra por comuna, desde z10,5.
 *
 * Hasta el #16 había además tres capas de círculos (halo, cuerpo y núcleo), un
 * anillo que latía sobre las comunas en riesgo y un `heatmap` cercano. Se
 * fueron: 36 puntos puestos donde vive la gente no son un campo de lluvia, y un
 * `heatmap` sobre una grilla pinta densidad, no mm/h (ver `rainRaster.ts`).
 *
 * # Jerarquía: por qué `beforeId` y por qué apunta al cono
 *
 * MapLibre no tiene z-index. El orden de dibujo es el orden del arreglo de capas
 * del estilo y la única forma de controlarlo es decir **antes de qué capa** se
 * inserta cada una. La lluvia es contexto: si tapa los pines de emergencia,
 * invierte la jerarquía del mapa.
 *
 * El ancla es `wind-cone-fill` porque **es la única capa propia que está montada
 * siempre**: si el ancla no existe en el momento del `addLayer`, MapLibre
 * descarta la capa entera.
 */

import type {
  ExpressionSpecification,
  RasterLayerSpecification,
  SymbolLayerSpecification,
} from 'maplibre-gl'
import {
  RAIN_FIELD_OPACITY,
  RAIN_TEXT,
  RAIN_TEXT_FADE,
  RAIN_TEXT_MIN_ZOOM,
  RAIN_TEXT_SIZE,
} from '@/domain/rainSymbology'

type RainTextLayerSpec = Omit<SymbolLayerSpecification, 'source'>
type RainFieldLayerSpec = Omit<RasterLayerSpecification, 'source'>

type Theme = 'light' | 'dark'

/** Fuente de las comunas (GeoJSON): etiquetas y texto. */
export const RAIN_SOURCE_ID = 'rain-forecast'
/** Fuente del campo (imagen). */
export const RAIN_FIELD_SOURCE_ID = 'rain-field-image'
export const RAIN_FIELD_LAYER_ID = 'rain-field'
export const RAIN_RISK_LABEL_LAYER_ID = 'rain-risk-label'
export const RAIN_TEXT_LAYER_ID = 'rain-text'

/** En orden de dibujo. Lo usa el re-anclaje tras un cambio de estilo. */
export const RAIN_LAYER_IDS = [
  RAIN_FIELD_LAYER_ID,
  RAIN_RISK_LABEL_LAYER_ID,
  RAIN_TEXT_LAYER_ID,
] as const

export const RAIN_BEFORE_ID = 'wind-cone-fill'

export const IS_FLOOD_RISK: ExpressionSpecification = [
  '==',
  ['get', 'riesgo_inundacion'],
  true,
]

export function rainFieldLayer(theme: Theme, visible: boolean): RainFieldLayerSpec {
  return {
    id: RAIN_FIELD_LAYER_ID,
    type: 'raster',
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'raster-opacity': RAIN_FIELD_OPACITY[theme],
      // Suaviza al acercarse: la celda del modelo mide ~16 km, y un borde
      // nítido de píxel insinuaría una precisión que no tiene.
      'raster-resampling': 'linear',
      // Sin fundido al cambiar de foto: cada hora cambia la imagen entera.
      'raster-fade-duration': 0,
    },
  }
}

/**
 * «Viña del Mar · riesgo de anegamiento», a escala regional.
 *
 * Reemplaza al anillo que latía. El campo dice cuánto llueve; esto dice dónde
 * esa lluvia cruzó el umbral de `riesgo_inundacion`, que es otra cosa (depende
 * del acumulado y de la pendiente). Desde z10,5 lo releva `rain-text`, que ya
 * pinta esas comunas con su color de riesgo.
 */
export function rainRiskLabelLayer(theme: Theme, visible: boolean): RainTextLayerSpec {
  const style = RAIN_TEXT[theme]
  return {
    id: RAIN_RISK_LABEL_LAYER_ID,
    type: 'symbol',
    maxzoom: RAIN_TEXT_MIN_ZOOM,
    filter: IS_FLOOD_RISK,
    layout: {
      visibility: visible ? 'visible' : 'none',
      'text-field': [
        'format',
        ['get', 'comuna'],
        { 'font-scale': 1 },
        '\n',
        {},
        'riesgo de anegamiento',
        { 'font-scale': 0.85 },
      ] as unknown as ExpressionSpecification,
      'text-size': 11.5,
      'text-max-width': 12,
      'text-line-height': 1.2,
      'text-padding': 4,
    },
    paint: {
      'text-color': style.colorRisk,
      'text-halo-color': style.halo,
      'text-halo-width': style.haloWidth,
      'text-halo-blur': style.haloBlur,
    },
  }
}

/* ------------------------------------------------------------------------- */
/* Capa de texto: el pronóstico sin clic                                      */
/* ------------------------------------------------------------------------- */

/**
 * Probabilidad, con el separador incluido. `''` cuando el modelo no la publica.
 *
 * `probabilidad_max` es **legítimamente `null`**: no todos los modelos de
 * Open-Meteo emiten la variable. El truco es `["to-string", …] == ""`, que es
 * la única forma limpia de detectar el nulo dentro de una expresión —
 * `["has", …]` devuelve `true` porque la clave existe, sólo que con valor nulo,
 * y un `number-format` sobre nulo escribiría `0 %`. Anunciar 0 % de
 * probabilidad cuando lo que pasa es que no se sabe sería inventar un dato
 * tranquilizador; se omite la línea y queda el milimetraje, que sí es cierto.
 */
const PROBABILITY_TEXT: ExpressionSpecification = [
  'case',
  ['==', ['to-string', ['get', 'probabilidad_max']], ''],
  '',
  [
    'concat',
    ['number-format', ['get', 'probabilidad_max'], { locale: 'es-CL', 'max-fraction-digits': 0 }],
    '% · ',
  ],
]

/**
 * Acumulado de la ventana, un decimal.
 *
 * `mm_total` y no `mm_hora_max`: "milímetros esperados" es lo que va a caer,
 * mientras que `mm_hora_max` es la punta que alimenta el radio y el flag de
 * riesgo. Mostrar la punta como si fuera el total inflaría la cifra por un
 * factor de veinte en una lluvia larga y suave.
 *
 * `number-format` con `locale: 'es-CL'` para que el separador decimal sea la
 * coma. `to-string` de un número daría `18.4` con punto, que en Chile se lee
 * como separador de miles.
 */
const MILLIMETERS_TEXT: ExpressionSpecification = [
  'concat',
  [
    'number-format',
    ['to-number', ['get', 'mm_total'], 0],
    { locale: 'es-CL', 'min-fraction-digits': 1, 'max-fraction-digits': 1 },
  ],
  ' mm',
]

/**
 * Ventana horaria, precedida de su salto de línea.
 *
 * El salto va DENTRO del `case` y no fuera: con `ventana` vacía —marcas de
 * tiempo que no parsearon— un `\n` incondicional dejaría una tercera línea en
 * blanco, y el bloque quedaría descentrado respecto a su punto sin que se vea
 * por qué.
 */
const WINDOW_TEXT: ExpressionSpecification = [
  'case',
  ['==', ['to-string', ['get', 'ventana']], ''],
  '',
  ['concat', '\n', ['get', 'ventana']],
]

/**
 * El bloque completo.
 *
 * ```
 *   Viña del Mar
 *   60% · 18,4 mm
 *   14:00 → 09:00 +1 d
 * ```
 *
 * # Por qué aparece el nombre de la comuna, que nadie pidió
 *
 * Porque esta capa se lo quita al basemap. MapLibre resuelve las colisiones de
 * etiquetas recorriendo el estilo **de arriba hacia abajo** (`PauseablePlacement`
 * arranca en `order.length - 1` y decrementa), así que la capa que está más
 * arriba coloca primero y gana. La lluvia va por encima de la cartografía de
 * CARTO, o sea que su bloque desplaza el topónimo "Viña del Mar" del basemap.
 * Sin repetirlo acá, el resultado neto de encender la capa sería un `60% ·
 * 18,4 mm` flotando sobre una ciudad que acaba de perder su nombre.
 *
 * `format` en vez de un `concat` plano por el `font-scale`: la comuna a tamaño
 * completo y los datos algo menores dan la jerarquía de lectura sin necesitar
 * una segunda fuente —que además habría que verificar que el endpoint de
 * glifos de CARTO sirva—. El escalado de un SDF es gratis.
 */
const RAIN_TEXT_FIELD = [
  'format',
  ['get', 'comuna'],
  { 'font-scale': 1 },
  '\n',
  {},
  ['concat', PROBABILITY_TEXT, MILLIMETERS_TEXT],
  { 'font-scale': 0.92 },
  WINDOW_TEXT,
  { 'font-scale': 0.86 },
] as unknown as ExpressionSpecification

/**
 * Bloque de pronóstico legible sin clic.
 *
 * # Jerarquía
 *
 * Comparte el `beforeId` de las manchas y se monta la ÚLTIMA, así que queda
 * justo debajo del cono: por encima de los cuatro discos de lluvia —que es
 * donde tiene que estar para leerse— y por debajo del cono, del radio sísmico,
 * de los sismos, de los incidentes y de los pines de cortes, que son marcadores
 * del DOM y viven fuera del lienzo. **Ningún pin de emergencia queda tapado**:
 * los incidentes son capas `circle` y el orden del arreglo también manda en el
 * orden de dibujo, así que se pintan encima de este texto.
 *
 * # Coste
 *
 * Es la primera capa de símbolo propia del mapa, y las de símbolo no son
 * gratis: MapLibre recalcula colisiones en cada frame de movimiento. Lo que lo
 * mantiene despreciable es el `minzoom`, que la excluye del recorrido de
 * colisiones por completo mientras no se llegue a z10,5 — que es la mayor parte
 * del tiempo, porque el mapa arranca a escala regional.
 */
export function rainTextLayer(theme: Theme, visible: boolean): RainTextLayerSpec {
  const style = RAIN_TEXT[theme]
  const [fadeFrom, fadeTo] = RAIN_TEXT_FADE
  const sizeStops = RAIN_TEXT_SIZE.flatMap(([zoom, size]) => [zoom, size])

  return {
    id: RAIN_TEXT_LAYER_ID,
    type: 'symbol',
    // Corte duro: saca la capa del cálculo de colisiones, no sólo del dibujo.
    minzoom: RAIN_TEXT_MIN_ZOOM,
    layout: {
      visibility: visible ? 'visible' : 'none',
      'text-field': RAIN_TEXT_FIELD,
      /*
       * Sin `text-font`: el defecto de MapLibre es `["Open Sans Regular",
       * "Arial Unicode MS Regular"]` y el endpoint de glifos de CARTO sirve
       * "Open Sans Regular" en los dos estilos. Nombrar una fuente que el
       * endpoint no tenga no rompe el estilo: simplemente **no se dibuja
       * ninguna letra**, con un error en consola que es fácil pasar por alto.
       */
      'text-size': [
        'interpolate',
        ['linear'],
        ['zoom'],
        ...sizeStops,
      ] as unknown as ExpressionSpecification,
      /*
       * Anclaje variable en vez de un `text-offset` fijo.
       *
       * El disco crece con el zoom y con la intensidad: cualquier
       * desplazamiento fijo que funcione a z11 queda dentro del núcleo a z14.
       * Con `text-variable-anchor` MapLibre prueba las cuatro posiciones y se
       * queda con la primera que no colisione, así que dos comunas vecinas se
       * apartan solas en vez de tapar una a la otra.
       */
      'text-variable-anchor': ['top', 'bottom', 'left', 'right'],
      'text-radial-offset': 1.3,
      'text-justify': 'auto',
      // "Viña del Mar" y "Villa Alemana" caben en una línea; el defecto de 10
      // em las partiría.
      'text-max-width': 14,
      'text-line-height': 1.25,
      'text-padding': 4,
      /*
       * Que colisione, que es justo lo que se quiere: treinta y seis bloques de
       * tres líneas superpuestos no serían legibles. MapLibre descarta los que
       * no caben y al acercarse van apareciendo.
       */
      'text-allow-overlap': false,
      'text-ignore-placement': false,
      /*
       * Prioridad de colocación: el riesgo primero.
       *
       * Se colocan en orden ascendente de esta clave, así que cuando dos
       * bloques se pisan sobrevive el de la comuna en riesgo. Sin esto el
       * desempate lo decidiría el orden de los features en el GeoJSON — o sea,
       * el azar.
       */
      'symbol-sort-key': ['case', IS_FLOOD_RISK, 0, 1],
    },
    paint: {
      'text-color': ['case', IS_FLOOD_RISK, style.colorRisk, style.color],
      'text-halo-color': style.halo,
      'text-halo-width': style.haloWidth,
      'text-halo-blur': style.haloBlur,
      /*
       * El desvanecido. `minzoom` ya cortó en seco por debajo; esto evita que
       * el bloque aparezca de golpe justo al cruzar el umbral.
       */
      'text-opacity': ['interpolate', ['linear'], ['zoom'], fadeFrom, 0, fadeTo, 1],
    },
  }
}
