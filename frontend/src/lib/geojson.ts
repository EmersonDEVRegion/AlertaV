/**
 * Conversión de incidentes tipados a GeoJSON para MapLibre.
 *
 * Dos decisiones que importan:
 *
 * 1. La fuente de datos del mapa se construye desde `/incidents/active`, no
 *    desde `/incidents/geojson`. El backend ofrece ambos —y desde la v2.0.0 los
 *    dos traen `confidence_level`—, pero la ficha necesita el objeto tipado
 *    completo igual; usar una sola llamada evita dos vistas del mundo que
 *    pueden desincronizarse.
 *
 * 2. Sólo viajan propiedades escalares. MapLibre serializa los arreglos y
 *    objetos anidados de las propiedades de un feature, así que `sources` no se
 *    incluye: el mapa sólo necesita lo justo para colorear, y `code` para
 *    volver al objeto real.
 */

import type { FeatureCollection, Point } from 'geojson'
import type { ConfidenceLevel, Incident } from '@/api/types'
import { type IncidentLayerKey, layerOf } from '@/domain/families'
import { isClosed, levelOf, needsVerificationCaveat } from '@/domain/symbology'

export interface IncidentFeatureProps {
  code: string
  /**
   * Capa a la que pertenece el incidente: decide con qué paleta se pinta y qué
   * casilla lo enciende. Se precalcula acá porque la API no manda `family` y
   * las expresiones de estilo no pueden derivarla.
   */
  layer: IncidentLayerKey
  /** Tramo de la política v2.0.0. Decide el color del relleno. */
  confidence_level: ConfidenceLevel
  /** Estado cerrado → mismo color atenuado y anillo punteado. */
  is_closed: boolean
  /** Tramo `confirmed` sin que nadie haya ido al lugar → punto central hueco. */
  unverified_confirmed: boolean
  /** '' en vez de null: simplifica el filtro de la capa de anillo. */
  alert_level: string
  confidence: number
  is_official_confirmed: boolean
}

export type IncidentFeatureCollection = FeatureCollection<Point, IncidentFeatureProps>

export function toFeatureCollection(
  incidents: readonly Incident[],
): IncidentFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: incidents.map((incident) => ({
      type: 'Feature',
      // Sin `id` a nivel de feature: MapLibre exige enteros y `code` es texto.
      // La fuente declara `promoteId: 'code'`, que sí acepta identificadores de
      // texto, y el resalte del seleccionado se hace con un filtro sobre `code`.
      geometry: { type: 'Point', coordinates: [incident.lon, incident.lat] },
      properties: {
        code: incident.code,
        layer: layerOf(incident.type),
        confidence_level: levelOf(incident),
        is_closed: isClosed(incident.status),
        unverified_confirmed: needsVerificationCaveat(incident),
        alert_level: incident.alert_level ?? '',
        confidence: incident.confidence,
        is_official_confirmed: incident.is_official_confirmed,
      },
    })),
  }
}
