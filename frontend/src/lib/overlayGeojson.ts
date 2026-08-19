/**
 * Polígonos derivados que se superponen al mapa: radio de percepción sísmica y
 * cono de propagación por viento.
 *
 * Se construyen en coordenadas reales (ver `lib/geo.ts`) y viajan como fuentes
 * GeoJSON propias, separadas de las de incidentes y sismos. El motivo es que
 * son **datos calculados, no observados**: mezclarlos en la misma fuente que la
 * señal cruda haría muy fácil que un día alguien los tratara como lo mismo.
 */

import type { Feature, FeatureCollection, Polygon } from 'geojson'
import type { SeismicEvent } from '@/api/seismicTypes'
import type { Incident } from '@/api/types'
import { circleRing, sectorRing } from '@/lib/geo'
import { perceptionRadiusKm } from '@/domain/seismicReach'
import type { WindCone } from '@/domain/windCone'

// ---------------------------------------------------------------------------
// Radio de percepción sísmica
// ---------------------------------------------------------------------------

export interface ReachProps {
  usgs_id: string
  radius_km: number
  magnitude: number | null
}

export type ReachCollection = FeatureCollection<Polygon, ReachProps>

const EMPTY_REACH: ReachCollection = { type: 'FeatureCollection', features: [] }

export function toReachCollection(
  events: readonly SeismicEvent[],
): ReachCollection {
  const features: Feature<Polygon, ReachProps>[] = []

  for (const event of events) {
    const radiusKm = perceptionRadiusKm(event)
    // `null` significa «no se puede afirmar que se sintiera»: sin magnitud, o
    // el foco más profundo que el propio alcance. No se dibuja nada.
    if (radiusKm === null) continue

    features.push({
      type: 'Feature',
      geometry: {
        type: 'Polygon',
        coordinates: [circleRing(event.lon, event.lat, radiusKm)],
      },
      properties: {
        usgs_id: event.usgs_id,
        radius_km: Math.round(radiusKm),
        magnitude: event.magnitude,
      },
    })
  }

  return features.length > 0
    ? { type: 'FeatureCollection', features }
    : EMPTY_REACH
}

// ---------------------------------------------------------------------------
// Cono de viento
// ---------------------------------------------------------------------------

export interface ConeProps {
  code: string
  bearing_deg: number
  length_km: number
}

export type ConeCollection = FeatureCollection<Polygon, ConeProps>

export const EMPTY_CONE: ConeCollection = { type: 'FeatureCollection', features: [] }

/**
 * Cuña de un único incidente: sólo se dibuja para el incendio seleccionado,
 * porque el viento se consulta a un servicio externo y pedirlo para cada
 * incidente del mapa sería una llamada por marcador.
 */
export function toConeCollection(
  incident: Incident | null,
  cone: WindCone | null,
): ConeCollection {
  if (!incident || !cone) return EMPTY_CONE

  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [
            sectorRing(
              incident.lon,
              incident.lat,
              cone.bearingDeg,
              cone.halfAngleDeg,
              cone.lengthKm,
            ),
          ],
        },
        properties: {
          code: incident.code,
          bearing_deg: Math.round(cone.bearingDeg),
          length_km: Number(cone.lengthKm.toFixed(1)),
        },
      },
    ],
  }
}
