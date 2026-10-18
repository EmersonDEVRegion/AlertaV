/**
 * Cliente de `GET /api/v1/events/transito/geojson`: el estado del tránsito que
 * publica @TTIValparaiso (MTT), con el tramo dibujado sobre la calle.
 *
 * Ruta RELATIVA a `env.apiBaseUrl`, como las demás (ver `roadClosures.ts`).
 *
 * Se valida cada feature: sin `public_id` o con un subtipo que esta versión de
 * la app no conoce, se descarta. La geometría puede faltar (`null`): el MTT
 * nombró un lugar que no encontramos. Esa feature va a la lista de «Ahora»
 * pero no al mapa.
 */

import type { Feature, FeatureCollection, LineString, MultiLineString, Point } from 'geojson'
import { apiGet } from './client'
import { isFiniteNumber, isRecord, trimmedText } from './parse'

export const TRANSITO_SUBTIPOS = [
  'congestion',
  'lento',
  'semaforo',
  'pista_ocupada',
  'marcha',
  'normalidad',
] as const

export type TransitoSubtipo = (typeof TRANSITO_SUBTIPOS)[number]

export interface TransitoProperties {
  public_id: string
  subtipo: TransitoSubtipo
  /** «Congestión», «Semáforo apagado»… */
  nombre: string
  /** «Congestión en Viana, de Sucre a Von Schroeders». */
  titulo: string
  tramo: string | null
  sentido: string | null
  comuna: string | null
  /** El tweet del MTT. */
  url: string | null
  text: string
  timestamp: string
  /** Cuándo deja de mostrarse si nadie avisa antes la normalidad. */
  vence: string
  lat: number | null
  lon: number | null
}

export type TransitoGeometry = LineString | MultiLineString | Point

export type TransitoFeature = Feature<TransitoGeometry | null, TransitoProperties>

export type TransitoCollection = FeatureCollection<TransitoGeometry | null, TransitoProperties>

export const EMPTY_TRANSITO: TransitoCollection = { type: 'FeatureCollection', features: [] }

const GEOMETRIAS = new Set(['LineString', 'MultiLineString', 'Point'])

function esSubtipo(value: unknown): value is TransitoSubtipo {
  return typeof value === 'string' && (TRANSITO_SUBTIPOS as readonly string[]).includes(value)
}

function geometria(raw: unknown): TransitoGeometry | null {
  if (!isRecord(raw) || typeof raw.type !== 'string' || !GEOMETRIAS.has(raw.type)) return null
  if (!Array.isArray(raw.coordinates) || raw.coordinates.length === 0) return null
  return raw as unknown as TransitoGeometry
}

function parseFeature(raw: unknown): TransitoFeature | null {
  if (!isRecord(raw) || !isRecord(raw.properties)) return null
  const p = raw.properties
  const id = trimmedText(p.public_id)
  const timestamp = trimmedText(p.timestamp)
  if (!id || !esSubtipo(p.subtipo) || !timestamp) return null
  const nombre = trimmedText(p.nombre) ?? p.subtipo
  return {
    type: 'Feature',
    id,
    geometry: geometria(raw.geometry),
    properties: {
      public_id: id,
      subtipo: p.subtipo,
      nombre,
      titulo: trimmedText(p.titulo) ?? nombre,
      tramo: trimmedText(p.tramo),
      sentido: trimmedText(p.sentido),
      comuna: trimmedText(p.comuna),
      url: trimmedText(p.url),
      text: trimmedText(p.text) ?? '',
      timestamp,
      vence: trimmedText(p.vence) ?? timestamp,
      lat: isFiniteNumber(p.lat) ? p.lat : null,
      lon: isFiniteNumber(p.lon) ? p.lon : null,
    },
  }
}

export function parseTransito(payload: unknown): TransitoCollection {
  const body = isRecord(payload) ? payload : {}
  const raw = Array.isArray(body.features) ? body.features : []
  const features = raw.map(parseFeature).filter((f): f is TransitoFeature => f !== null)
  return features.length === 0 ? EMPTY_TRANSITO : { type: 'FeatureCollection', features }
}

export async function fetchTransito(signal?: AbortSignal): Promise<TransitoCollection> {
  return parseTransito(await apiGet<unknown>('/events/transito/geojson', signal))
}

/** La caja `[oeste, sur, este, norte]` de una feature, o `null` sin geometría. */
export function boundsOfTransito(
  feature: TransitoFeature,
): [number, number, number, number] | null {
  const g = feature.geometry
  if (!g) return null
  const puntos: number[][] =
    g.type === 'Point'
      ? [g.coordinates]
      : g.type === 'LineString'
        ? g.coordinates
        : g.coordinates.flat()
  let [oeste, sur, este, norte] = [Infinity, Infinity, -Infinity, -Infinity]
  for (const [lon, lat] of puntos) {
    if (!isFiniteNumber(lon) || !isFiniteNumber(lat)) continue
    oeste = Math.min(oeste, lon)
    este = Math.max(este, lon)
    sur = Math.min(sur, lat)
    norte = Math.max(norte, lat)
  }
  return Number.isFinite(oeste) ? [oeste, sur, este, norte] : null
}
