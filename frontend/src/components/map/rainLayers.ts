/**
 * Capas de MapLibre para la lluvia pronosticada.
 *
 * Cinco capas, de abajo hacia arriba:
 *
 *   1. `rain-halo`      — mancha ancha y muy difusa. Da la sensación de nube.
 *   2. `rain-core`      — el cuerpo. Color por `riesgo_inundacion`, radio por
 *                         `mm_hora_max`.
 *   3. `rain-nucleus`   — disco interior, un paso más caliente de la rampa.
 *   4. `rain-risk-ring` — anillo fino, **sólo** sobre las comunas con el flag.
 *                         Contorno estático de las comunas en riesgo.
 *   5. `rain-text`      — el pronóstico en letra, a partir de z10,5. La única
 *                         capa de símbolo del mapa.
 *
 * # Jerarquía: por qué `beforeId` y por qué apunta al cono
 *
 * MapLibre no tiene z-index. El orden de dibujo es el orden del arreglo de capas
 * del estilo y la única forma de controlarlo es decir **antes de qué capa** se
 * inserta cada una. La lluvia es contexto: si tapa los pines de emergencia,
 * invierte la jerarquía del mapa.
 *
 * El ancla es `wind-cone-fill` y no una capa de incidentes ni el radio sísmico,
 * por una razón muy concreta: **es la única capa propia que está montada
 * siempre**. Las de incidentes viven bajo `showIncidents` y las de sismos bajo
 * `showSeismic`; si el ancla no existe en el momento del `addLayer`, MapLibre
 * emite un `error` y **descarta la capa entera** —no la dibuja arriba, no la
 * dibuja en absoluto—. Anclar a algo condicional sería un modo de falla que sólo
 * aparece cuando el usuario apaga otra capa primero.
 *
 * Queda: amenaza sísmica → lluvia → radio sísmico → cono → sismos → incidentes,
 * y los pines de cortes por encima de todo por ser marcadores del DOM.
 */

import type {
  CircleLayerSpecification,
  ExpressionSpecification,
  HeatmapLayerSpecification,
  SymbolLayerSpecification,
} from 'maplibre-gl'
import {
  RAIN_CIRCLE_MAX_ZOOM,
  RAIN_HEAT,
  RAIN_HEAT_INTENSITY,
  RAIN_HEAT_MIN_ZOOM,
  RAIN_HEAT_RADIUS,
  RAIN_MM_MAX,
  RAIN_MM_MIN,
  RAIN_PALETTE,
  RAIN_SWAP,
  RAIN_TEXT,
  RAIN_TEXT_FADE,
  RAIN_TEXT_MIN_ZOOM,
  RAIN_TEXT_SIZE,
  RAIN_ZOOM_STOPS,
} from '@/domain/rainSymbology'

export type RainLayerSpec = Omit<CircleLayerSpecification, 'source'>
export type RainTextLayerSpec = Omit<SymbolLayerSpecification, 'source'>
export type RainHeatLayerSpec = Omit<HeatmapLayerSpecification, 'source'>

type Theme = 'light' | 'dark'

export const RAIN_SOURCE_ID = 'rain-forecast'
export const RAIN_HEAT_LAYER_ID = 'rain-heat'
export const RAIN_HALO_LAYER_ID = 'rain-halo'
export const RAIN_CORE_LAYER_ID = 'rain-core'
export const RAIN_NUCLEUS_LAYER_ID = 'rain-nucleus'
export const RAIN_RISK_RING_LAYER_ID = 'rain-risk-ring'
export const RAIN_TEXT_LAYER_ID = 'rain-text'

/**
 * En orden de dibujo. Lo usa el re-anclaje tras un cambio de estilo.
 *
 * El mapa de calor va el PRIMERO —o sea, el más abajo—: es el campo continuo
 * sobre el que se apoyan los discos durante el solape, y dejarlo encima
 * ensuciaría el núcleo de las comunas en riesgo justo en la ventana de zoom en
 * la que las dos representaciones conviven.
 */
export const RAIN_LAYER_IDS = [
  RAIN_HEAT_LAYER_ID,
  RAIN_HALO_LAYER_ID,
  RAIN_CORE_LAYER_ID,
  RAIN_NUCLEUS_LAYER_ID,
  RAIN_RISK_RING_LAYER_ID,
  RAIN_TEXT_LAYER_ID,
] as const

/**
 * Ancla del orden de dibujo. Ver la nota de la cabecera.
 *
 * Hay un test que ata esta constante al `id` real de `overlayLayers.ts`: si
 * alguien renombra esa capa, el test cae antes que el mapa.
 */
export const RAIN_BEFORE_ID = 'wind-cone-fill'

/**
 * El booleano de riesgo, en una sola expresión reutilizada por el filtro y por
 * los `case` de pintura.
 *
 * `["==", ..., true]` con un booleano literal y no `["get", "riesgo_inundacion"]`
 * a secas: si el backend regresara y mandara la cadena `"true"`, un `case` sobre
 * el `get` reventaría el estilo completo, mientras que esta comparación degrada a
 * "sin riesgo". `api/rain.ts` normaliza el campo antes de que llegue acá y avisa
 * por consola, así que el caso no debería existir; esto es el segundo cinturón.
 */
export const IS_FLOOD_RISK: ExpressionSpecification = [
  '==',
  ['get', 'riesgo_inundacion'],
  true,
]

/**
 * Intensidad como número.
 *
 * `to-number` con respaldo: una comuna sin la variable no revienta la expresión,
 * se dibuja en el extremo bajo de la rampa.
 */
const INTENSITY: ExpressionSpecification = ['to-number', ['get', 'mm_hora_max'], 0]

const round = (value: number) => Math.round(value * 100) / 100

/**
 * Construye un `circle-radius` con `["zoom"]` como expresión **raíz**.
 *
 * MapLibre compila el estilo una vez por nivel de zoom entero, así que necesita
 * saber de antemano qué parte depende del zoom: `["zoom"]` sólo puede ser la
 * entrada de un `step` o un `interpolate` de nivel superior, nunca ir dentro de
 * un `*`, un `+` o un `max`. La forma correcta invierte la estructura: el
 * `interpolate` sobre el zoom envuelve todo y dentro de cada tope va la
 * expresión de datos —la interpolación por intensidad—, que es lo que MapLibre
 * sí sabe evaluar por feature.
 *
 * `scale` y `pad` se aplican en JS sobre los topes, no como operaciones de la
 * expresión. Es lo que impide que una capa derivada vuelva a anidar el zoom.
 */
function rainRadius(scale = 1, pad = 0): ExpressionSpecification {
  const stops = RAIN_ZOOM_STOPS.flatMap(([zoom, atMin, atMax]) => {
    const byIntensity: ExpressionSpecification = [
      'interpolate',
      ['linear'],
      INTENSITY,
      RAIN_MM_MIN,
      round(atMin * scale + pad),
      RAIN_MM_MAX,
      round(atMax * scale + pad),
    ]
    return [zoom, byIntensity]
  })

  return ['interpolate', ['linear'], ['zoom'], ...stops] as unknown as ExpressionSpecification
}

/**
 * Envuelve una opacidad para que se desvanezca al entrar en el dominio del
 * mapa de calor.
 *
 * # La estructura no es negociable
 *
 * `["zoom"]` sólo puede ser la entrada de un `interpolate` de nivel superior.
 * La forma ingenua —multiplicar la expresión de datos por un factor de zoom—
 * dejaría el zoom anidado y **tiraría el estilo completo**, que es el error que
 * este repositorio ya pagó dos veces. Así que el `interpolate` sobre el zoom va
 * afuera y la expresión por feature va DENTRO de cada tope.
 *
 * En el tope superior el valor es un `0` literal y no la expresión apagada: al
 * final del desvanecido no hay nada que distinguir entre una comuna en riesgo y
 * una sin riesgo, y escribir el `case` dos veces sólo daría más trabajo al
 * compilador de estilos para llegar al mismo cero.
 */
function fadeOut(
  atRegional: number | ExpressionSpecification,
): ExpressionSpecification {
  const [from, to] = RAIN_SWAP
  return [
    'interpolate',
    ['linear'],
    ['zoom'],
    from,
    atRegional,
    to,
    0,
  ] as unknown as ExpressionSpecification
}

/** Interpolación sobre el zoom con topes escalares. */
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

/** Color por riesgo. El mismo `case` en las dos capas de fondo. */
function rainColor(theme: Theme): ExpressionSpecification {
  const palette = RAIN_PALETTE[theme]
  return ['case', IS_FLOOD_RISK, palette.risk, palette.rain]
}

/**
 * Color del núcleo: un paso más caliente de la misma rampa.
 *
 * `circle` no tiene degradados radiales, así que el salto de matiz entre el
 * cuerpo y el núcleo es lo único que aproxima la caída de intensidad de una
 * celda de radar. Con un color plano en los tres discos la pila sólo cambiaba
 * de opacidad y se leía como una mancha, no como un campo.
 */
function rainNucleusColor(theme: Theme): ExpressionSpecification {
  const palette = RAIN_PALETTE[theme]
  return ['case', IS_FLOOD_RISK, palette.nucleusRisk, palette.nucleus]
}

/**
 * Halo: la mancha ancha.
 *
 * `circle-blur: 1` difumina el borde en el propio fragment shader. Es la
 * alternativa barata al `fill-pattern`: cuesta unas pocas instrucciones por
 * píxel, no necesita textura, no se sube nada a la GPU en cada frame y el
 * resultado —un borde que se desvanece— es justo el lenguaje visual de "campo
 * atmosférico" que el dato pide.
 */
export function rainHaloLayer(theme: Theme, visible: boolean): RainLayerSpec {
  return {
    id: RAIN_HALO_LAYER_ID,
    type: 'circle',
    /*
     * Corte duro al entrar en el dominio del mapa de calor. Va más arriba que
     * el final del desvanecido a propósito: si el corte cayera dentro de la
     * rampa, la capa desaparecería de golpe a media opacidad.
     */
    maxzoom: RAIN_CIRCLE_MAX_ZOOM,
    /*
     * Se apaga por `visibility`, nunca desmontando la fuente. Desmontar
     * destruiría el GeoJSON ya subido al worker y volver a encender la capa
     * pagaría la subida otra vez. Con `visibility: 'none'` MapLibre deja de
     * dibujarla y ya.
     */
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'circle-radius': rainRadius(1.45),
      'circle-color': rainColor(theme),
      'circle-opacity': fadeOut(RAIN_PALETTE[theme].haloOpacity),
      'circle-blur': 1,
    },
  }
}

/** Cuerpo de la mancha. Menos difuso: da el centro sin dibujar un borde. */
export function rainCoreLayer(theme: Theme, visible: boolean): RainLayerSpec {
  const palette = RAIN_PALETTE[theme]
  return {
    id: RAIN_CORE_LAYER_ID,
    type: 'circle',
    maxzoom: RAIN_CIRCLE_MAX_ZOOM,
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'circle-radius': rainRadius(),
      'circle-color': rainColor(theme),
      // El riesgo también sube la opacidad: en escala de grises —o para quien no
      // distingue el azul claro del profundo— el contraste sigue leyéndose.
      'circle-opacity': fadeOut([
        'case',
        IS_FLOOD_RISK,
        palette.coreOpacityRisk,
        palette.coreOpacity,
      ]),
      'circle-blur': 0.55,
    },
  }
}

/**
 * Disco interior.
 *
 * # Por qué tres círculos y no uno
 *
 * MapLibre no tiene degradados radiales en la capa `circle`: `circle-blur`
 * difumina el borde, pero el relleno es plano. Apilar tres discos concéntricos
 * —halo muy difuso y tenue, cuerpo intermedio, núcleo pequeño y más denso— es
 * la forma barata de aproximar la caída radial de intensidad que tiene una zona
 * de precipitación real.
 *
 * Cada capa cuesta un `circle` sobre 36 puntos, así que el total sigue siendo
 * despreciable: es geometría estática que la GPU dibuja una vez y sólo repinta
 * al mover el mapa. Ninguna animación, ningún `setPaintProperty` por frame.
 */
export function rainNucleusLayer(theme: Theme, visible: boolean): RainLayerSpec {
  const palette = RAIN_PALETTE[theme]
  return {
    id: RAIN_NUCLEUS_LAYER_ID,
    type: 'circle',
    maxzoom: RAIN_CIRCLE_MAX_ZOOM,
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      // Poco más de la mitad del cuerpo: deja ver los dos escalones exteriores.
      'circle-radius': rainRadius(0.55),
      'circle-color': rainNucleusColor(theme),
      'circle-opacity': fadeOut([
        'case',
        IS_FLOOD_RISK,
        palette.nucleusOpacityRisk,
        palette.nucleusOpacity,
      ]),
      // Menos difuso que el cuerpo: el degradado se cierra hacia el centro.
      'circle-blur': 0.35,
    },
  }
}

/**
 * Anillo de riesgo. Sólo las comunas con el flag.
 *
 * Estática, como el resto de la capa. Antes latía con un `requestAnimationFrame`
 * que escribía `circle-stroke-opacity` a ~12 Hz; se quitó para no tener el mapa
 * repintando de forma permanente por un adorno.
 *
 * Lo que se perdió en movimiento se compensa con definición: trazo algo más
 * grueso, opacidad fija en el extremo alto del rango y un `circle-stroke-opacity`
 * que sube con el zoom. El anillo llama la atención por contraste, no por
 * moverse — que además es lo correcto para quien tenga `prefers-reduced-motion`
 * y para una pantalla que alguien mira de reojo.
 *
 * Conserva el `filter` porque sigue siendo cierto que sólo aplica a las comunas
 * con el flag: en un invierno normal, 0 a 3 features.
 */
export function rainRiskRingLayer(theme: Theme, visible: boolean): RainLayerSpec {
  const palette = RAIN_PALETTE[theme]
  const [, strong] = palette.ringOpacity

  return {
    id: RAIN_RISK_RING_LAYER_ID,
    type: 'circle',
    maxzoom: RAIN_CIRCLE_MAX_ZOOM,
    filter: IS_FLOOD_RISK,
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      // Un par de píxeles por fuera del cuerpo: el anillo debe leerse como
      // contorno de la mancha, no como un objeto aparte.
      'circle-radius': rainRadius(1, 3),
      'circle-color': 'transparent',
      'circle-stroke-color': palette.ring,
      /*
       * El grosor crece con el zoom. A escala regional el anillo es una
       * insinuación; al acercarse a una comuna se vuelve un contorno legible.
       * Un grosor fijo se ve grueso de lejos y raquítico de cerca.
       */
      'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 7, 1.2, 12, 2.4],
      /*
       * Se desvanece con sus discos.
       *
       * Sigue siendo un escalar por tope y **no** una expresión por feature:
       * eso es lo que importaba de la nota original. Una interpolación sobre el
       * zoom se compila una vez por nivel entero y viaja como uniform del
       * shader; una expresión data-driven obligaría a reconstruir el búfer de
       * vértices de pintura. Lo que se prohibió acá fue lo segundo.
       *
       * Sin este desvanecido el anillo quedaría flotando sobre el campo de
       * calor entre z12,6 y z13,2 —solo, sin la mancha que contorneaba— y se
       * leería como un objeto propio en vez de como un borde.
       */
      'circle-stroke-opacity': fadeOut(strong),
    },
  }
}

/* ------------------------------------------------------------------------- */
/* Campo de precipitación: el relevo local                                    */
/* ------------------------------------------------------------------------- */

/**
 * Peso de cada comuna: la intensidad de punta, normalizada.
 *
 * `heatmap-weight` espera un número sin unidad donde 1 es «un punto entero».
 * Los mm/h crudos (0,2 a 12) darían pesos de hasta doce veces lo previsto y la
 * rampa saturaría en su extremo caliente con una sola comuna — el mapa entero
 * del mismo azul profundo.
 *
 * El piso es 0,08 y no 0. Una comuna en el mínimo de emisión **está lloviendo**,
 * y con peso 0 desaparecería del campo: un hueco entre dos comunas con lluvia
 * se leería como un claro que el modelo no afirma.
 *
 * `RAIN_MM_MAX` es una cota de presentación, así que MapLibre satura por
 * encima. Un aguacero de 30 mm/h pesa lo mismo que uno de 12: es correcto,
 * porque a partir de ahí la diferencia ya no cabe en la rampa.
 */
function heatWeight(): ExpressionSpecification {
  return [
    'interpolate',
    ['linear'],
    INTENSITY,
    RAIN_MM_MIN,
    0.08,
    RAIN_MM_MAX,
    1,
  ] as ExpressionSpecification
}

/**
 * Rampa de densidad.
 *
 * `["heatmap-density"]` sólo existe dentro de esta propiedad, y esta expresión
 * **no admite `["zoom"]`**: el color del campo no puede depender de la escala.
 * Lo que sí depende del zoom son el radio, la intensidad y la opacidad.
 */
function heatColor(theme: Theme): ExpressionSpecification {
  return [
    'interpolate',
    ['linear'],
    ['heatmap-density'],
    ...RAIN_HEAT[theme].stops.flatMap(([density, color]) => [density, color]),
  ] as unknown as ExpressionSpecification
}

/**
 * Campo de precipitación local.
 *
 * # Por qué no lleva el flag de riesgo
 *
 * Es la decisión más importante de esta capa. Los discos codifican el riesgo
 * con color y con un anillo; el campo de calor **no lo hace y no debe hacerlo**.
 *
 * Un `heatmap` interpola entre puntos vecinos: el color de un píxel a mitad de
 * camino entre dos comunas no pertenece a ninguna de las dos. Si el riesgo
 * tiñera la rampa, ese píxel intermedio afirmaría un riesgo de inundación sobre
 * un territorio para el que el backend nunca lo calculó — y `riesgo_inundacion`
 * es un umbral evaluado **por comuna**, no un campo continuo.
 *
 * El campo dice intensidad y sólo intensidad. El riesgo lo siguen diciendo el
 * bloque de texto, que sí es por comuna y sigue montado a esta escala, y el
 * panel de referencia.
 */
export function rainHeatLayer(theme: Theme, visible: boolean): RainHeatLayerSpec {
  return {
    id: RAIN_HEAT_LAYER_ID,
    type: 'heatmap',
    /*
     * Corte duro por debajo. Saca la capa del pipeline a escala regional, donde
     * 36 puntos repartidos en 300 km no forman un campo sino lunares.
     */
    minzoom: RAIN_HEAT_MIN_ZOOM,
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'heatmap-weight': heatWeight(),
      'heatmap-intensity': byZoom(RAIN_HEAT_INTENSITY),
      'heatmap-color': heatColor(theme),
      'heatmap-radius': byZoom(RAIN_HEAT_RADIUS),
      /*
       * Entra donde los discos se van. Los dos extremos salen de `RAIN_SWAP`,
       * así que la suma de opacidades no se hunde a mitad de camino: no hay un
       * zoom en el que la lluvia casi no se vea.
       */
      'heatmap-opacity': byZoom([
        [RAIN_SWAP[0], 0],
        [RAIN_SWAP[1], RAIN_HEAT[theme].opacity],
      ]),
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
