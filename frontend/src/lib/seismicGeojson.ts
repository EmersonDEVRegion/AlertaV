/**
 * Conversión de sismos tipados a GeoJSON para MapLibre.
 *
 * Mismo criterio que con los incidentes: se consume el listado tipado, se arma
 * el GeoJSON acá, y sólo viajan escalares —MapLibre serializa los objetos
 * anidados de las propiedades de un feature.
 *
 * `band` y `sizing_magnitude` se precalculan para que las expresiones de estilo
 * sean un `match` y una interpolación sobre valores ya resueltos, y no una
 * segunda copia de los umbrales.
 */

import type { FeatureCollection, Point } from 'geojson'
import type { SeismicEvent } from '@/api/seismicTypes'
import { bandOf, sizingMagnitude } from '@/domain/seismicSymbology'

export interface SeismicFeatureProps {
  usgs_id: string
  band: string
  /** Magnitud acotada a [2, 7]. La usa la interpolación del radio. */
  sizing_magnitude: number
  magnitude: number | null
  depth_km: number | null
  review_status: string
}

export type SeismicFeatureCollection = FeatureCollection<Point, SeismicFeatureProps>

export function toSeismicFeatureCollection(
  events: readonly SeismicEvent[],
): SeismicFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: events.map((event) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [event.lon, event.lat] },
      properties: {
        usgs_id: event.usgs_id,
        band: bandOf(event),
        sizing_magnitude: sizingMagnitude(event.magnitude),
        magnitude: event.magnitude,
        depth_km: event.depth_km,
        // '' en vez de null: simplifica la comparación en la expresión de estilo.
        review_status: event.review_status ?? '',
      },
    })),
  }
}
