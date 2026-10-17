/**
 * Cliente de `GET /api/v1/feed/avisos-meteo/zonas`: las zonas de pronóstico de
 * la DMC que tocan los avisos vigentes, como GeoJSON para el mapa.
 *
 * Se valida cada feature: una zona sin geometría o con una severidad fuera de
 * 1–3 se descarta en vez de llegar a MapLibre.
 */

import type { Feature, FeatureCollection, MultiPolygon, Polygon } from 'geojson'
import { apiGet } from './client'
import { isRecord, trimmedText } from './parse'
import { METEO_WARNING_TYPES, type MeteoWarningType } from './meteoWarningTypes'

interface MeteoZoneProperties {
  zona: string
  nombre: string
  severidad: 1 | 2 | 3
  tipo: MeteoWarningType
  avisos: number
  fenomenos: string
  /** Ids de los avisos separados por coma (ver el backend: MapLibre y las listas). */
  ids: string
}

export type MeteoZoneCollection = FeatureCollection<Polygon | MultiPolygon, MeteoZoneProperties>

export const EMPTY_METEO_ZONES: MeteoZoneCollection = { type: 'FeatureCollection', features: [] }

function parseFeature(raw: unknown): Feature<Polygon | MultiPolygon, MeteoZoneProperties> | null {
  if (!isRecord(raw) || !isRecord(raw.geometry) || !isRecord(raw.properties)) return null
  const geometry = raw.geometry
  if (
    (geometry.type !== 'Polygon' && geometry.type !== 'MultiPolygon') ||
    !Array.isArray(geometry.coordinates)
  )
    return null
  const p = raw.properties
  const zona = trimmedText(p.zona)
  const severidad = p.severidad
  const tipo = p.tipo
  if (
    !zona ||
    (severidad !== 1 && severidad !== 2 && severidad !== 3) ||
    typeof tipo !== 'string' ||
    !(METEO_WARNING_TYPES as readonly string[]).includes(tipo)
  )
    return null
  return {
    type: 'Feature',
    geometry: geometry as unknown as Polygon | MultiPolygon,
    properties: {
      zona,
      nombre: trimmedText(p.nombre) ?? zona,
      severidad,
      tipo: tipo as MeteoWarningType,
      avisos: typeof p.avisos === 'number' ? p.avisos : 1,
      fenomenos: trimmedText(p.fenomenos) ?? '',
      ids: trimmedText(p.ids) ?? '',
    },
  }
}

export function parseMeteoZones(payload: unknown): MeteoZoneCollection {
  const body = isRecord(payload) ? payload : {}
  const raw = Array.isArray(body.features) ? body.features : []
  const features = raw
    .map(parseFeature)
    .filter((f): f is Feature<Polygon | MultiPolygon, MeteoZoneProperties> => f !== null)
  return features.length === 0 ? EMPTY_METEO_ZONES : { type: 'FeatureCollection', features }
}

export async function fetchMeteoZones(signal?: AbortSignal): Promise<MeteoZoneCollection> {
  return parseMeteoZones(await apiGet<unknown>('/feed/avisos-meteo/zonas', signal))
}

/** Caja [oeste, sur, este, norte] de las zonas de un aviso, o `null` si no toca ninguna. */
export function boundsOfWarning(
  data: MeteoZoneCollection,
  id: string,
): [number, number, number, number] | null {
  let west = Infinity
  let south = Infinity
  let east = -Infinity
  let north = -Infinity
  for (const feature of data.features) {
    if (!feature.properties.ids.split(',').includes(id)) continue
    const polygons =
      feature.geometry.type === 'Polygon'
        ? [feature.geometry.coordinates]
        : feature.geometry.coordinates
    for (const polygon of polygons)
      for (const ring of polygon)
        for (const [lon, lat] of ring) {
          if (lon === undefined || lat === undefined) continue
          west = Math.min(west, lon)
          east = Math.max(east, lon)
          south = Math.min(south, lat)
          north = Math.max(north, lat)
        }
  }
  return Number.isFinite(west) ? [west, south, east, north] : null
}
