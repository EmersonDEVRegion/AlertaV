/**
 * Cortes de luz sobre el lienzo, con agrupación.
 *
 * # Por qué dejaron de ser marcadores del DOM
 *
 * Eran un `<Marker>` por corte: un nodo del DOM que MapLibre reposiciona en cada
 * cuadro del paneo. Para que un temporal no trabara el mapa había un tope de 150
 * pines, y pasado el tope el resto desaparecía **sin aviso** mientras el panel
 * los seguía contando. Justo en el escenario en que más importan.
 *
 * El motivo original para usar DOM —registrar imágenes en el estilo y volver a
 * hacerlo tras cada `setStyle`— ya lo resolvió `useEmergencyIcons`, que es lo que
 * dibuja los demás iconos. Así que el corte usa el mismo camino: un disco del
 * color de la empresa y el rayo SDF (`av-bolt`) encima.
 *
 * # Por qué esta es la única fuente agrupada
 *
 * Un incendio no se agrupa jamás: un mapa de emergencias no puede esconder un
 * fuego detrás de un «12». Un corte de luz sí: en un temporal son decenas en la
 * misma comuna, y lo que se necesita saber a escala regional es dónde y a
 * cuántos clientes, que es exactamente lo que dice un racimo. Por eso el racimo
 * SUMA los clientes afectados, y el más grande se dibuja encima.
 *
 * Desde `OUTAGE_CLUSTER_MAX_ZOOM + 1` cada corte va suelto. Tocar un racimo
 * acerca la cámara hasta que se abre.
 */

import type {
  CircleLayerSpecification,
  ExpressionSpecification,
  FilterSpecification,
  SymbolLayerSpecification,
} from 'maplibre-gl'
import { OUTAGE_ICON } from '@/domain/emergencyIcons'
import { OUTAGE_CLUSTER, PROVIDER, UNKNOWN_PROVIDER } from '@/domain/powerSymbology'

type Theme = 'light' | 'dark'
type CircleLayer = Omit<CircleLayerSpecification, 'source'>
type SymbolLayer = Omit<SymbolLayerSpecification, 'source'>

export const OUTAGE_SOURCE_ID = 'outages'
export const OUTAGE_CLUSTER_LAYER_ID = 'outages-cluster'
export const OUTAGE_HIT_LAYER_ID = 'outages-hit'
export const OUTAGE_SELECTED_LAYER_ID = 'outages-selected'

/** Hasta este zoom se agrupan; desde el siguiente, cada corte va suelto. */
export const OUTAGE_CLUSTER_MAX_ZOOM = 11
export const OUTAGE_CLUSTER_RADIUS = 40

/**
 * Suma de clientes por racimo. `affected_clients` llega en 0 cuando no se
 * informó (ver `toOutageFeatureCollection`), así que la suma nunca es `null`.
 */
export const OUTAGE_CLUSTER_PROPERTIES = {
  clientes: ['+', ['get', 'affected_clients']],
}

const IS_CLUSTER: ExpressionSpecification = ['has', 'point_count']
const IS_PIN: ExpressionSpecification = ['!', ['has', 'point_count']]

/** Color de la empresa. Un corte sin empresa identificable va en gris, nunca con el color de otra. */
const PROVIDER_COLOR = [
  'match',
  ['get', 'provider'],
  'chilquinta',
  PROVIDER.chilquinta.color,
  'cge',
  PROVIDER.cge.color,
  UNKNOWN_PROVIDER.color,
] as unknown as ExpressionSpecification

/** El que afecta a más clientes se dibuja encima. */
const BY_CLIENTS: ExpressionSpecification = ['coalesce', ['get', 'affected_clients'], 0]
const BY_CLUSTER_CLIENTS: ExpressionSpecification = ['coalesce', ['get', 'clientes'], 0]

/** Borde del disco: el fondo de la app, para recortarlo contra el terreno. */
const EDGE: Record<Theme, string> = { light: '#ffffff', dark: '#0f172a' }
/** Anillo de selección: el color de máximo contraste del tema. */
const INK: Record<Theme, string> = { light: '#0f172a', dark: '#f8fafc' }

/**
 * Radio del disco según el zoom, más un margen fijo.
 *
 * El margen va DENTRO de cada parada y no como `['+', radio, n]`: MapLibre sólo
 * acepta `zoom` como entrada de un `interpolate` de primer nivel, y una suma por
 * fuera invalida la capa entera (el validador del test lo ataja).
 */
function pinRadius(pad = 0): ExpressionSpecification {
  return ['interpolate', ['linear'], ['zoom'], 7, 7 + pad, 11, 9 + pad, 15, 11 + pad]
}

const PIN_RADIUS = pinRadius()
const CLOSED_OPACITY: ExpressionSpecification = ['case', ['get', 'is_closed'], 0.55, 1]

export function outageClusterLayer(theme: Theme): CircleLayer {
  return {
    id: OUTAGE_CLUSTER_LAYER_ID,
    type: 'circle',
    filter: IS_CLUSTER,
    layout: { 'circle-sort-key': BY_CLUSTER_CLIENTS },
    paint: {
      'circle-color': OUTAGE_CLUSTER.color,
      'circle-radius': ['step', ['get', 'point_count'], 13, 10, 16, 50, 21],
      'circle-stroke-color': EDGE[theme],
      'circle-stroke-width': 2,
    },
  }
}

export function outageClusterCountLayer(): SymbolLayer {
  return {
    id: 'outages-cluster-count',
    type: 'symbol',
    filter: IS_CLUSTER,
    layout: {
      // Sin `text-font`: el defecto de MapLibre ("Open Sans Regular") lo sirve
      // el endpoint de glifos de CARTO en los dos estilos. Ver `rainLayers.ts`.
      'text-field': ['get', 'point_count_abbreviated'],
      'text-size': 12,
      'text-allow-overlap': true,
      'text-ignore-placement': true,
    },
    paint: { 'text-color': OUTAGE_CLUSTER.onColor },
  }
}

export function outagePinLayer(theme: Theme): CircleLayer {
  return {
    id: 'outages-pin',
    type: 'circle',
    filter: IS_PIN,
    layout: { 'circle-sort-key': BY_CLIENTS },
    paint: {
      'circle-color': PROVIDER_COLOR,
      'circle-radius': PIN_RADIUS,
      'circle-stroke-color': EDGE[theme],
      'circle-stroke-width': 2,
      'circle-opacity': CLOSED_OPACITY,
      'circle-stroke-opacity': CLOSED_OPACITY,
    },
  }
}

/** El rayo dentro del disco. Sólo se monta cuando los iconos ya están registrados. */
export function outageGlyphLayer(): SymbolLayer {
  return {
    id: 'outages-glyph',
    type: 'symbol',
    filter: IS_PIN,
    layout: {
      'icon-image': OUTAGE_ICON,
      'icon-size': ['interpolate', ['linear'], ['zoom'], 7, 0.2, 11, 0.26, 15, 0.32],
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
      'symbol-sort-key': BY_CLIENTS,
    },
    paint: {
      'icon-color': '#ffffff',
      'icon-opacity': CLOSED_OPACITY,
    },
  }
}

export function outageSelectedLayer(theme: Theme, code: string | null): CircleLayer {
  return {
    id: OUTAGE_SELECTED_LAYER_ID,
    type: 'circle',
    filter: ['all', IS_PIN, ['==', ['get', 'code'], code ?? ' ']] as FilterSpecification,
    paint: {
      'circle-radius': pinRadius(5),
      'circle-color': 'transparent',
      'circle-stroke-color': INK[theme],
      'circle-stroke-width': 2.5,
    },
  }
}

/** Objetivo táctil: más grande que el disco, invisible. Ver `hitLayer` de incidentes. */
export const outageHitLayer: CircleLayer = {
  id: OUTAGE_HIT_LAYER_ID,
  type: 'circle',
  filter: IS_PIN,
  paint: {
    'circle-radius': pinRadius(10),
    'circle-color': '#000000',
    'circle-opacity': 0,
  },
}
