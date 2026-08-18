/**
 * Conversion de incidentes tipados a GeoJSON para MapLibre.
 *
 * Dos decisiones que importan:
 *
 * 1. La fuente de datos del mapa se construye desde `/incidents/active`, no
 *    desde `/incidents/geojson`. El backend ofrece ambos, pero la tarjeta de
 *    detalle necesita el objeto tipado completo igual; usar una sola llamada
 *    evita dos vistas del mundo que pueden desincronizarse.
 *
 * 2. Solo viajan propiedades escalares. MapLibre serializa los arrays y objetos
 *    anidados de las propiedades de un feature, así que `sources` no se incluye:
 *    el mapa solo necesita lo justo para colorear, y `code` para volver al
 *    objeto real.
 */

import type { FeatureCollection, Point } from 'geojson'
import type { Incident } from '@/api/types'
import { phenomenonKey } from '@/domain/symbology'

export interface IncidentFeatureProps {
  code: string
  phenomenon: string
  /** '' en vez de null: simplifica el filtro de la capa de alerta. */
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
        phenomenon: phenomenonKey(incident),
        alert_level: incident.alert_level ?? '',
        confidence: incident.confidence,
        is_official_confirmed: incident.is_official_confirmed,
      },
    })),
  }
}
