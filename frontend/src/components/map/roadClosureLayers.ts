/**
 * Capas de MapLibre para los cortes e intervenciones de la vía.
 *
 * Cuatro capas, de abajo hacia arriba:
 *
 *   1. `closure-cut-ring` — anillo pulsante, **sólo** sobre los cortes efectivos
 *                           (`severidad >= 4`). Es el único elemento animado.
 *   2. `closure-body`     — el rombo. Color y tamaño por `severidad`; el MTT,
 *                           que no la trae, cae en su propio tono apagado.
 *   3. `closure-mtt-dot`  — punto central hueco sobre los avisos del MTT.
 *   4. `closure-hit`      — objetivo de toque, invisible y generoso.
 *
 * # La forma: rombos, no discos
 *
 * `circle-pitch-alignment: 'map'` y un `circle-stroke` grueso no bastan para
 * distinguir un corte de una emergencia a simple vista, y la distinción importa
 * más que en cualquier otra capa: `road_closure` está fuera de
 * `CORRELATABLE_EVENT_TYPES` y entra con confianza 0,0 — no es un siniestro y no
 * puede parecerlo. La diferencia se consigue con el rombo del sprite del estilo
 * base... que no existe en Positron ni en Dark Matter.
 *
 * Así que la forma se consigue con lo que `circle` sí sabe hacer: un disco
 * pequeño con trazo grueso y **opacidad de relleno baja en el MTT**, más un
 * punto central hueco. La silueta resultante es un anillo, no un disco lleno, y
 * eso ya lo separa de los pines de emergencia sin depender de ningún sprite.
 * Documentado acá para que nadie "arregle" el relleno translúcido pensando que
 * es un descuido.
 *
 * # Jerarquía: por qué `beforeId` y por qué apunta al cono
 *
 * MapLibre no tiene z-index. El orden de dibujo es el orden del arreglo de capas
 * del estilo y la única forma de controlarlo es decir **antes de qué capa** se
 * inserta cada una. Un corte es contexto: si tapa los pines de emergencia,
 * invierte la jerarquía del mapa.
 *
 * El ancla es `wind-cone-fill`, la misma que usa la lluvia, y por la misma razón
 * exacta: **es la única capa propia que está montada siempre**. Las de
 * incidentes viven bajo `showIncidents` y las de sismos bajo `showSeismic`; si
 * el ancla no existe en el momento del `addLayer`, MapLibre emite un `error` y
 * **descarta la capa entera** —no la dibuja arriba, no la dibuja en absoluto—.
 * Anclar a algo condicional sería un modo de falla que sólo aparece cuando el
 * usuario apaga otra capa primero.
 */

import type { CircleLayerSpecification, ExpressionSpecification } from 'maplibre-gl'
import {
  CUT_RING_GROWTH,
  ROAD_CLOSURE_PALETTE,
  ROAD_CLOSURE_ZOOM_STOPS,
  SEVERITY_CUT,
  SEVERITY_MAX,
  SEVERITY_MIN,
  type Theme,
} from '@/domain/roadClosureSymbology'

export type RoadClosureLayerSpec = Omit<CircleLayerSpecification, 'source'>

export const ROAD_CLOSURE_SOURCE_ID = 'road-closures'
export const CLOSURE_CUT_RING_LAYER_ID = 'closure-cut-ring'
export const CLOSURE_BODY_LAYER_ID = 'closure-body'
export const CLOSURE_MTT_DOT_LAYER_ID = 'closure-mtt-dot'
export const CLOSURE_HIT_LAYER_ID = 'closure-hit'

/** En orden de dibujo. Lo usa el re-anclaje tras un cambio de estilo. */
export const ROAD_CLOSURE_LAYER_IDS = [
  CLOSURE_CUT_RING_LAYER_ID,
  CLOSURE_BODY_LAYER_ID,
  CLOSURE_MTT_DOT_LAYER_ID,
  CLOSURE_HIT_LAYER_ID,
] as const

/**
 * Ancla del orden de dibujo. Ver la nota de la cabecera.
 *
 * Hay un test que ata esta constante al `id` real de `overlayLayers.ts`: si
 * alguien renombra esa capa, el test cae antes que el mapa.
 */
export const ROAD_CLOSURE_BEFORE_ID = 'wind-cone-fill'

/**
 * ¿Trae severidad? O sea: ¿viene del MOP?
 *
 * `["has", …]` y no `["!=", ["get", …], null]`, que es la forma que parece
 * equivalente y no lo es: `get` sobre una clave ausente devuelve `null`, pero
 * también lo devuelve una clave presente con valor nulo, y esas dos cosas son
 * justo las que esta capa tiene que distinguir. El backend omite la clave
 * entera cuando no hay escala publicada (ver `_road_closure_feature`), de modo
 * que `has` responde exactamente la pregunta que se quiere hacer.
 *
 * La consecuencia visible de equivocarse acá sería un aviso del MTT pintado
 * como «transitable, gravedad mínima» — afirmarle al usuario que alguien midió
 * algo que nadie midió.
 */
export const HAS_SEVERITY: ExpressionSpecification = ['has', 'severidad']

/**
 * La severidad como número, con respaldo.
 *
 * El respaldo es `SEVERITY_MIN` y NO importa para el MTT —esa rama ya está
 * cortada por `HAS_SEVERITY` antes de llegar acá—: cubre el caso de un `_mop`
 * que trajera la clave con basura dentro. Un corte con severidad ilegible se
 * dibuja pequeño y ámbar, que es lo que corresponde a «no se sabe».
 */
const SEVERITY: ExpressionSpecification = ['to-number', ['get', 'severidad'], SEVERITY_MIN]

/** ¿Es un corte efectivo? Lo usan el filtro del anillo y su tamaño. */
export const IS_CUT: ExpressionSpecification = ['>=', SEVERITY, SEVERITY_CUT]

const round = (value: number) => Math.round(value * 100) / 100

/**
 * Construye un `circle-radius` con `["zoom"]` como expresión **raíz**.
 *
 * MapLibre compila el estilo una vez por nivel de zoom entero, así que necesita
 * saber de antemano qué parte depende del zoom: `["zoom"]` sólo puede ser la
 * entrada de un `step` o un `interpolate` de nivel superior, nunca ir dentro de
 * un `*`, un `+` o un `max`. La forma correcta invierte la estructura: el
 * `interpolate` sobre el zoom envuelve todo y dentro de cada tope va la
 * expresión de datos —la interpolación por severidad—, que es lo que MapLibre
 * sí sabe evaluar por feature.
 *
 * `scale` y `pad` se aplican en JS sobre los topes, no como operaciones de la
 * expresión. Es lo que impide que una capa derivada vuelva a anidar el zoom, y
 * es la razón de que exista esta fábrica en vez de una constante exportada que
 * las capas de arriba multiplicarían. Ese atajo ya tiró el estilo entero dos
 * veces en este repositorio.
 */
function closureRadius(scale = 1, pad = 0): ExpressionSpecification {
  const stops = ROAD_CLOSURE_ZOOM_STOPS.flatMap(([zoom, atMin, atMax]) => {
    const bySeverity: ExpressionSpecification = [
      'interpolate',
      ['linear'],
      SEVERITY,
      SEVERITY_MIN,
      round(atMin * scale + pad),
      SEVERITY_MAX,
      round(atMax * scale + pad),
    ]
    return [zoom, bySeverity]
  })

  return ['interpolate', ['linear'], ['zoom'], ...stops] as unknown as ExpressionSpecification
}

/**
 * Color del cuerpo: la jerarquía cromática entera, en una expresión.
 *
 * Primero se separa el MTT —que no tiene escala y no puede entrar en la rampa—
 * y sólo dentro de la rama del MOP se interpola por severidad.
 *
 * Los topes de la rampa son 0, 2, 3 y `SEVERITY_CUT`, y esa distribución no es
 * estética: `severity_rank` en el backend hace `transito * 2 + (1 si grave)`,
 * de modo que el salto a «no se puede pasar» ocurre en 4. Poner ahí el salto al
 * rojo es lo que hace que el color responda la pregunta operativa —¿puedo
 * pasar?— sin leer ninguna etiqueta.
 *
 * `interpolate` y no `step` a propósito: dentro de cada tramo la transición es
 * continua, así que un 3 se ve claramente más caliente que un 2 sin inventar
 * una quinta categoría. Lo que sí es un salto duro es el paso a rojo, y se
 * consigue poniendo dos topes juntos (3 → naranja, 4 → rojo).
 */
export function closureColor(theme: Theme): ExpressionSpecification {
  const palette = ROAD_CLOSURE_PALETTE[theme]
  const bySeverity: ExpressionSpecification = [
    'interpolate',
    ['linear'],
    SEVERITY,
    SEVERITY_MIN,
    palette.low,
    2,
    palette.mid,
    3,
    palette.mid,
    SEVERITY_CUT,
    palette.high,
    SEVERITY_MAX,
    palette.high,
  ] as unknown as ExpressionSpecification

  return ['case', HAS_SEVERITY, bySeverity, palette.mtt]
}

/** Opacidad del relleno: el MTT va más translúcido. Ver la nota de la forma. */
function closureFillOpacity(theme: Theme): ExpressionSpecification {
  const palette = ROAD_CLOSURE_PALETTE[theme]
  return ['case', HAS_SEVERITY, palette.fillOpacity, palette.fillOpacityMtt]
}

const hidden = (visible: boolean) => ({
  // Se apaga por `visibility`, nunca desmontando la fuente. Desmontar
  // destruiría el GeoJSON ya subido al worker y volver a encender la capa
  // pagaría la subida otra vez.
  layout: { visibility: visible ? ('visible' as const) : ('none' as const) },
})

/**
 * Anillo sobre los cortes efectivos: los que no se pueden pasar.
 *
 * Va el PRIMERO —o sea, el más abajo— para que el rombo se dibuje encima y el
 * anillo se lea como un halo, no como un borde.
 *
 * # Estático, y eso es una decisión ya tomada en este repositorio
 *
 * La versión obvia late: un `requestAnimationFrame` escribiendo
 * `circle-stroke-opacity` a ~12 Hz. La capa de lluvia tuvo exactamente eso y
 * **se quitó** —ver `rainRiskRingLayer`— para no dejar el mapa repintando de
 * forma permanente por un adorno. Repetirlo acá reintroduciría el mismo costo
 * en la capa que menos lo justifica: un corte de ruta lleva días o semanas
 * vigente, así que no hay ninguna urgencia que un parpadeo esté comunicando.
 *
 * Lo que se pierde en movimiento se compensa con contraste: trazo más grueso,
 * opacidad fija en el extremo alto del rango (`ringOpacity[1]`) y el rojo más
 * saturado de la paleta en el centro. El anillo llama la atención por contraste
 * y no por moverse — que además es lo correcto para quien tenga
 * `prefers-reduced-motion` y para una pantalla que alguien mira de reojo.
 */
export function closureCutRingLayer(theme: Theme, visible: boolean): RoadClosureLayerSpec {
  const palette = ROAD_CLOSURE_PALETTE[theme]
  const [, strong] = palette.ringOpacity

  return {
    id: CLOSURE_CUT_RING_LAYER_ID,
    type: 'circle',
    // Sólo los cortes efectivos. El filtro va acá y no en una opacidad a cero
    // para que MapLibre no pague el dibujo de un anillo invisible por cada
    // aviso del MTT, que son la mayoría de la capa.
    filter: IS_CUT,
    ...hidden(visible),
    paint: {
      'circle-radius': closureRadius(CUT_RING_GROWTH),
      'circle-color': 'transparent',
      'circle-stroke-color': palette.cutRing,
      'circle-stroke-width': 1.75,
      'circle-stroke-opacity': strong,
      'circle-pitch-alignment': 'map',
    },
  }
}

/** El cuerpo: color y tamaño por severidad, tono aparte para el MTT. */
export function closureBodyLayer(theme: Theme, visible: boolean): RoadClosureLayerSpec {
  const palette = ROAD_CLOSURE_PALETTE[theme]
  return {
    id: CLOSURE_BODY_LAYER_ID,
    type: 'circle',
    ...hidden(visible),
    paint: {
      'circle-radius': closureRadius(),
      'circle-color': closureColor(theme),
      'circle-opacity': closureFillOpacity(theme),
      'circle-stroke-color': palette.stroke,
      'circle-stroke-width': 1.25,
      'circle-stroke-opacity': palette.strokeOpacity,
      'circle-pitch-alignment': 'map',
    },
  }
}

/**
 * Punto central hueco sobre los avisos del MTT.
 *
 * Es la marca de «esto no tiene escala». Repite el mismo recurso que ya usan
 * los incidentes `confirmed` que ninguna fuente verificó en terreno: un hueco
 * en el centro significa, en todo este mapa, «falta información», y reusar el
 * vocabulario vale más que inventar un símbolo nuevo para lo mismo.
 */
export function closureMttDotLayer(theme: Theme, visible: boolean): RoadClosureLayerSpec {
  const palette = ROAD_CLOSURE_PALETTE[theme]
  return {
    id: CLOSURE_MTT_DOT_LAYER_ID,
    type: 'circle',
    filter: ['!', HAS_SEVERITY],
    ...hidden(visible),
    paint: {
      // Un tercio del cuerpo, con el mismo `scale` de la fábrica para que no
      // se despegue al cambiar de zoom.
      'circle-radius': closureRadius(0.32),
      'circle-color': palette.stroke,
      'circle-opacity': 0.9,
      'circle-pitch-alignment': 'map',
    },
  }
}

/**
 * Objetivo de toque: invisible y más grande que el rombo.
 *
 * Un rombo de 5 px a nivel regional es imposible de tocar con el dedo. El radio
 * mínimo es el que recomienda la guía de accesibilidad para un objetivo táctil,
 * y por eso el `pad` es aditivo: la capa crece con el zoom igual que el cuerpo,
 * pero nunca baja de ese piso.
 */
export function closureHitLayer(visible: boolean): RoadClosureLayerSpec {
  return {
    id: CLOSURE_HIT_LAYER_ID,
    type: 'circle',
    ...hidden(visible),
    paint: {
      'circle-radius': closureRadius(1, 9),
      'circle-color': 'transparent',
      'circle-opacity': 0,
    },
  }
}
