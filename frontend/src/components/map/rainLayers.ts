/**
 * Capas de MapLibre para la lluvia pronosticada.
 *
 * Tres capas, de abajo hacia arriba:
 *
 *   1. `rain-halo`      — mancha ancha y muy difusa. Da la sensación de nube.
 *   2. `rain-core`      — el cuerpo. Color por `riesgo_inundacion`, radio por
 *                         `mm_hora_max`.
 *   3. `rain-risk-ring` — anillo fino, **sólo** sobre las comunas con el flag.
 *                         Es la única que se anima. Ver `hooks/useRainPulse.ts`.
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

import type { CircleLayerSpecification, ExpressionSpecification } from 'maplibre-gl'
import { RAIN_MM_MAX, RAIN_MM_MIN, RAIN_PALETTE, RAIN_ZOOM_STOPS } from '@/domain/rainSymbology'

export type RainLayerSpec = Omit<CircleLayerSpecification, 'source'>

type Theme = 'light' | 'dark'

export const RAIN_SOURCE_ID = 'rain-forecast'
export const RAIN_HALO_LAYER_ID = 'rain-halo'
export const RAIN_CORE_LAYER_ID = 'rain-core'
export const RAIN_RISK_RING_LAYER_ID = 'rain-risk-ring'

/** En orden de dibujo. Lo usa el re-anclaje tras un cambio de estilo. */
export const RAIN_LAYER_IDS = [
  RAIN_HALO_LAYER_ID,
  RAIN_CORE_LAYER_ID,
  RAIN_RISK_RING_LAYER_ID,
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

/** Color por riesgo. El mismo `case` en las dos capas de fondo. */
function rainColor(theme: Theme): ExpressionSpecification {
  const palette = RAIN_PALETTE[theme]
  return ['case', IS_FLOOD_RISK, palette.risk, palette.rain]
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
     * Se apaga por `visibility`, nunca desmontando la fuente. Desmontar
     * destruiría el GeoJSON ya subido al worker y volver a encender la capa
     * pagaría la subida otra vez. Con `visibility: 'none'` MapLibre deja de
     * dibujarla y ya.
     */
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'circle-radius': rainRadius(1.45),
      'circle-color': rainColor(theme),
      'circle-opacity': RAIN_PALETTE[theme].haloOpacity,
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
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      'circle-radius': rainRadius(),
      'circle-color': rainColor(theme),
      // El riesgo también sube la opacidad: en escala de grises —o para quien no
      // distingue el azul claro del profundo— el contraste sigue leyéndose.
      'circle-opacity': ['case', IS_FLOOD_RISK, palette.coreOpacityRisk, palette.coreOpacity],
      'circle-blur': 0.55,
    },
  }
}

/**
 * Anillo de riesgo. Sólo las comunas con el flag.
 *
 * Es la única capa animada, y por eso es la única con un `filter`: el pulso
 * escribe una propiedad de pintura constante sobre ella y no sobre las 36
 * comunas. En un invierno normal esto son 0 a 3 features.
 */
export function rainRiskRingLayer(theme: Theme, visible: boolean): RainLayerSpec {
  const palette = RAIN_PALETTE[theme]
  const [, resting] = palette.ringOpacity

  return {
    id: RAIN_RISK_RING_LAYER_ID,
    type: 'circle',
    filter: IS_FLOOD_RISK,
    layout: { visibility: visible ? 'visible' : 'none' },
    paint: {
      // Un par de píxeles por fuera del cuerpo: el anillo debe leerse como
      // contorno de la mancha, no como un objeto aparte.
      'circle-radius': rainRadius(1, 3),
      'circle-color': 'transparent',
      'circle-stroke-color': palette.ring,
      'circle-stroke-width': 1.6,
      // Valor en reposo. El pulso lo sobrescribe mientras corre y lo restituye
      // al terminar, así que este es también el estado con `prefers-reduced-motion`.
      'circle-stroke-opacity': resting,
      /*
       * Transición en 0 a propósito.
       *
       * MapLibre interpola las propiedades de pintura en 300 ms por defecto. El
       * pulso escribe cada ~80 ms, así que con el valor por defecto cada
       * escritura encolaría su propia interpolación: el mapa quedaría repintando
       * a 60 fps de forma permanente para animar algo que ya viene animado desde
       * fuera. Con `duration: 0` cada escritura es un salto y el mapa repinta
       * exactamente una vez por escritura.
       */
      'circle-stroke-opacity-transition': { duration: 0, delay: 0 },
    },
  }
}
