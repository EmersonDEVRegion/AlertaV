/**
 * Capas de MapLibre para los incidentes — política de confianza v2.0.0.
 *
 * Orden de dibujo, de abajo hacia arriba:
 *
 *   0. `uncertainty`  — círculo en metros: dónde puede estar el hecho.
 *   1. `alert-halo`   — anillo exterior. Eje SENAPRED.
 *   2. `casing`       — placa del tema con aro de severidad. Bajo el icono.
 *   3. `core`         — disco relleno. Tramo de `confidence_level`.
 *   4. `closed-ring`  — anillo punteado sobre los incidentes ya cerrados.
 *   5. `unverified`   — punto central hueco: `confirmed` sin verificación.
 *   6. `selected`     — realce del incidente abierto en la ficha.
 *   7. `hit`          — círculo invisible y generoso. Sólo existe para el dedo.
 *
 * El `casing` no es decorativo. Desde la v2.0.0 el color puede ser rojo
 * (#dc2626) o amarillo (#eab308), y el anillo de alerta de SENAPRED también es
 * rojo (#e11d48) o amarillo (#f59e0b). Sin una placa neutra en medio, un
 * incidente `unsafe` con alerta roja sería una mancha roja uniforme y los dos
 * ejes se volverían ilegibles justo cuando más importan.
 *
 * La placa toma el fondo del tema (`PIN_EDGE`): blanca en claro, pizarra casi
 * negra en oscuro. Fue blanca fija mientras debajo había un disco de color; desde
 * que el icono reemplazó al disco quedó sola, y en el mapa oscuro era un manchón
 * blanco bajo cada glifo. El aro fino del color de severidad es lo que dibuja
 * la silueta del marcador cuando la placa se confunde con el terreno.
 *
 * Ningún color se escribe acá: salen de `domain/palette`, que resuelve cuál de
 * las tres paletas —incendios, tráfico u otras emergencias— aplica según la
 * familia del incidente, y que también alimenta la leyenda y la ficha.
 *
 * Las tres familias comparten estas mismas capas en vez de tener una cada una:
 * el radio, el aro blanco, el anillo de alerta y la marca de no verificado son
 * idénticos: lo único que cambia es el color. Duplicar las capas por familia
 * habría triplicado el costo de render para variar un `match`.
 */

import type { CircleLayerSpecification, ExpressionSpecification } from 'maplibre-gl'
import { LEVEL_COLOR_EXPRESSION } from '@/domain/palette'
import { ALERT_COLOR_EXPRESSION } from '@/domain/symbology'
import type { Theme } from '@/hooks/useTheme'
import { PIN_EDGE, PIN_INK } from './pinTheme'

export type IncidentLayer = Omit<CircleLayerSpecification, 'source'>

export const INCIDENT_SOURCE_ID = 'incidents'
export const INCIDENT_HIT_LAYER_ID = 'incidents-hit'

/**
 * Radio del disco. Crece con el zoom y, dentro de cada zoom, los tramos con más
 * evidencia se dibujan más grandes: la jerarquía visual sigue a la informativa,
 * y no al revés. Es lo que impide que el rojo de `unsafe` —que es una
 * advertencia sobre el dato— domine el mapa por encima de un incendio real.
 */
const SIZE_BY_LEVEL: ExpressionSpecification = [
  'match',
  ['get', 'confidence_level'],
  'confirmed',
  1.0,
  'possible',
  0.78,
  'unsafe',
  0.58,
  0.78,
]

/**
 * El de más evidencia se dibuja encima.
 *
 * Los iconos llevan `icon-allow-overlap` —un racimo apretado es información, no
 * ruido—, así que cuando dos se pisan alguno queda debajo. Sin orden explícito
 * decide el orden de llegada; con esto, un confirmado nunca queda tapado por un
 * reporte sin corroborar. Exportado para la capa de iconos.
 */
export const BY_EVIDENCE: ExpressionSpecification = [
  'match',
  ['get', 'confidence_level'],
  'confirmed',
  3,
  'possible',
  2,
  1,
]

const round = (value: number) => Math.round(value * 100) / 100

/** Radio base del disco, en píxeles, antes de aplicar el factor por tramo. */
const ZOOM_STOPS = [
  [6, 7],
  [10, 13],
  [14, 20],
] as const

/**
 * Relleno del anillo de alerta: 7 px en z6 y 11 px en z14, lineal.
 *
 * No es un `interpolate` propio sumado al radio del disco: el zoom vive una sola
 * vez —en la raíz—, así que el relleno se evalúa en JS para cada tope y entra
 * como número constante.
 */
const alertPad = (zoom: number) => 7 + (zoom - 6) * 0.5

type RadiusOptions = {
  /** Factor sobre el radio base, se multiplica junto al tramo de confianza. */
  scale?: number
  /** Píxeles fijos que se suman después. Constante o función del zoom. */
  pad?: number | ((zoom: number) => number)
}

/**
 * Construye un `circle-radius` con `["zoom"]` como expresión **raíz**.
 *
 * MapLibre GL JS sólo acepta `["zoom"]` como entrada de un `step` o un
 * `interpolate` de nivel superior: no puede aparecer dentro de un `*`, un `+`
 * ni un `max`, porque el estilo se compila una vez por nivel de zoom entero y
 * necesita saber de antemano qué parte depende del zoom. La versión anterior
 * hacía `["*", SIZE_BY_LEVEL, ["interpolate", ..., ["zoom"], ...]]`, y las capas
 * derivadas encima sumaban con `["+", CORE_RADIUS, n]`, dejando el zoom dos
 * niveles adentro. De ahí el error fatal.
 *
 * La estructura queda invertida: el `interpolate` sobre el zoom envuelve todo, y
 * dentro de cada tope va la expresión de datos —el `match` sobre
 * `confidence_level` y el `pad`— que es lo que MapLibre sí sabe evaluar por
 * feature. El resultado numérico es idéntico al del diseño original.
 */
function circleRadius({ scale = 1, pad = 0 }: RadiusOptions = {}): ExpressionSpecification {
  const stops = ZOOM_STOPS.flatMap(([zoom, base]) => {
    const sized: ExpressionSpecification = ['*', SIZE_BY_LEVEL, round(base * scale)]
    const padding = typeof pad === 'function' ? pad(zoom) : pad
    const value: ExpressionSpecification =
      padding === 0 ? sized : ['+', sized, round(padding)]
    return [zoom, value]
  })

  return ['interpolate', ['linear'], ['zoom'], ...stops] as unknown as ExpressionSpecification
}

const CORE_RADIUS = circleRadius()

const HALO_RADIUS = circleRadius({ pad: alertPad })

/**
 * Metros por píxel en el ecuador con zoom 0, para mosaicos de 512 px como los
 * de MapLibre, ya multiplicado por el coseno de la latitud de la región (33° S).
 * Entre los 32° y los 34° S el error es de menos de un 2 %: el círculo es una
 * cota, no una medición.
 */
const METROS_POR_PIXEL_Z0 = 78_271.517 * Math.cos((33 * Math.PI) / 180)

/** `incertidumbre_m` → píxeles. Exponencial en base 2: exacto en todo zoom. */
function metrosAPixeles(zoom: number): ExpressionSpecification {
  return ['*', ['get', 'incertidumbre_m'], round((2 ** zoom / METROS_POR_PIXEL_Z0) * 1e4) / 1e4]
}

/**
 * Círculo de incertidumbre: hasta dónde puede estar el hecho según la precisión
 * del punto (ver `domain/locationPrecision`).
 *
 * **Sólo el del incidente abierto, y sólo el contorno.** Un disco relleno
 * alrededor de cada emergencia se lee como su zona afectada o un radio de
 * evacuación, que es lo contrario de lo que dice. Con la ficha abierta, el
 * rótulo «Ubicación aproximada (± 200 m)» está al lado y explica el trazo.
 *
 * Desde zoom 13, que es donde 60 m empiezan a verse: más lejos el disco del
 * incidente ya los tapa.
 */
export function uncertaintyLayer(code: string | null): IncidentLayer {
  return {
    id: 'incidents-uncertainty',
    type: 'circle',
    minzoom: 13,
    filter: [
      'all',
      ['==', ['get', 'code'], code ?? ' '],
      ['>', ['get', 'incertidumbre_m'], 0],
    ],
    paint: {
      'circle-radius': [
        'interpolate',
        ['exponential', 2],
        ['zoom'],
        13,
        metrosAPixeles(13),
        22,
        metrosAPixeles(22),
      ] as unknown as ExpressionSpecification,
      'circle-color': 'transparent',
      'circle-stroke-color': LEVEL_COLOR_EXPRESSION as unknown as ExpressionSpecification,
      'circle-stroke-width': 1.5,
      'circle-stroke-opacity': ['interpolate', ['linear'], ['zoom'], 13, 0, 14, 0.7],
      'circle-pitch-alignment': 'map',
    },
  }
}

/** Sólo los incidentes con alerta oficial vigente llevan anillo. */
const HAS_ALERT: ExpressionSpecification = ['!=', ['get', 'alert_level'], '']

export const alertHaloLayer: IncidentLayer = {
  id: 'incidents-alert-halo',
  type: 'circle',
  filter: HAS_ALERT,
  layout: { 'circle-sort-key': BY_EVIDENCE },
  paint: {
    'circle-radius': HALO_RADIUS,
    'circle-color': ALERT_COLOR_EXPRESSION as unknown as ExpressionSpecification,
    'circle-opacity': 0.16,
    'circle-stroke-color': ALERT_COLOR_EXPRESSION as unknown as ExpressionSpecification,
    'circle-stroke-width': 2,
    'circle-stroke-opacity': 0.9,
  },
}

/** Opacidad de los cerrados: color atenuado y menos presencia. */
const CLOSED_OPACITY: ExpressionSpecification = ['case', ['get', 'is_closed'], 0.55, 0.95]

/**
 * Placa bajo el icono: fondo del tema y aro del color de severidad. Separa el
 * glifo del anillo de alerta y del terreno. Ver nota de arriba.
 */
export function casingLayer(theme: Theme): IncidentLayer {
  return {
    id: 'incidents-casing',
    type: 'circle',
    layout: { 'circle-sort-key': BY_EVIDENCE },
    paint: {
      'circle-radius': circleRadius({ pad: 2.5 }),
      'circle-color': PIN_EDGE[theme],
      'circle-opacity': 0.95,
      'circle-stroke-color': LEVEL_COLOR_EXPRESSION as unknown as ExpressionSpecification,
      'circle-stroke-width': 1.5,
      'circle-stroke-opacity': CLOSED_OPACITY,
    },
  }
}

export const coreLayer: IncidentLayer = {
  id: 'incidents-core',
  type: 'circle',
  layout: { 'circle-sort-key': BY_EVIDENCE },
  paint: {
    'circle-radius': CORE_RADIUS,
    'circle-color': LEVEL_COLOR_EXPRESSION as unknown as ExpressionSpecification,
    'circle-opacity': CLOSED_OPACITY,
  },
}

/**
 * Anillo punteado de los incidentes cerrados.
 *
 * MapLibre no tiene trazo punteado en la capa de círculos, así que el efecto se
 * consigue con un anillo fino de color apagado — suficiente para leer "esto ya
 * no está activo" sin robarle el canal del color al tramo de confianza.
 */
const CLOSED_RING: Record<Theme, string> = { light: '#475569', dark: '#94a3b8' }

export function closedRingLayer(theme: Theme): IncidentLayer {
  return {
    id: 'incidents-closed-ring',
    type: 'circle',
    filter: ['get', 'is_closed'],
    paint: {
      'circle-radius': circleRadius({ pad: 1 }),
      'circle-color': 'transparent',
      'circle-stroke-color': CLOSED_RING[theme],
      'circle-stroke-width': 1.5,
      'circle-stroke-opacity': 0.85,
    },
  }
}

/**
 * Punto central hueco para los `confirmed` que ninguna fuente verificó en
 * terreno. Es la marca que impide que el naranja se lea como "CONAF confirmó".
 * Va del color de la placa: un agujero en el glifo, no un punto blanco encima.
 */
export function unverifiedLayer(theme: Theme): IncidentLayer {
  return {
    id: 'incidents-unverified',
    type: 'circle',
    filter: ['get', 'unverified_confirmed'],
    paint: {
      'circle-radius': circleRadius({ scale: 0.34 }),
      'circle-color': PIN_EDGE[theme],
      'circle-opacity': 0.92,
    },
  }
}

export function selectedLayer(code: string | null, theme: Theme): IncidentLayer {
  return {
    id: 'incidents-selected',
    type: 'circle',
    filter: ['==', ['get', 'code'], code ?? ' '],
    paint: {
      'circle-radius': circleRadius({ pad: 7 }),
      'circle-color': 'transparent',
      'circle-stroke-color': PIN_INK[theme],
      'circle-stroke-width': 3,
    },
  }
}

/**
 * Objetivo táctil. Las guías de accesibilidad piden ~44 px y el disco visible
 * mide menos que eso en zooms bajos, así que la superficie que recibe el toque
 * se separa de la que se ve.
 */
export const hitLayer: IncidentLayer = {
  id: INCIDENT_HIT_LAYER_ID,
  type: 'circle',
  paint: {
    'circle-radius': circleRadius({ pad: 12 }),
    'circle-color': '#000000',
    'circle-opacity': 0,
  },
}
