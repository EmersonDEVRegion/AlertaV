/**
 * Cuarteles de Bomberos de la V Región.
 *
 * Instantánea **estática** del SIG de Bomberos de Chile (sig.bomberos.cl),
 * tomada a mano con `backend/scripts/sig_bomberos.py` y servida por
 * `GET /api/v1/events/cuarteles`. No dice qué compañía despacha a cada lugar ni
 * si un cuartel está operativo: sólo dónde está.
 */

import { CUARTELES_SOURCE_URL } from '@/config/map'

type CuartelTipo = 'cuerpo' | 'compania'

interface CuartelProps {
  tipo: CuartelTipo
  nombre: string
  cuerpo: string
  /** Número de compañía («Quinta» → 5). `null` en el cuartel del Cuerpo. */
  numero: number | null
  direccion: string | null
  comuna: string | null
}

export interface Cuartel extends CuartelProps {
  id: string
  lon: number
  lat: number
}

interface CuartelFeature {
  type: 'Feature'
  geometry: { type: 'Point'; coordinates: [number, number] }
  properties: CuartelProps & { id: string }
}

export interface CuartelesData {
  collection: { type: 'FeatureCollection'; features: CuartelFeature[] }
  cuarteles: Cuartel[]
  /** «SIG Bomberos de Chile (sig.bomberos.cl)». */
  fuente: string
  /** ISO de cuando se generó la instantánea. */
  generado: string | null
}

export const EMPTY_CUARTELES: CuartelesData = {
  collection: { type: 'FeatureCollection', features: [] },
  cuarteles: [],
  fuente: 'SIG Bomberos de Chile',
  generado: null,
}

export class CuartelesLoadError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'CuartelesLoadError'
  }
}

function texto(valor: unknown): string | null {
  return typeof valor === 'string' && valor.trim() ? valor.trim() : null
}

/** Valida la forma y descarta lo que no se puede dibujar. */
export function parseCuarteles(payload: unknown): CuartelesData {
  if (typeof payload !== 'object' || payload === null) {
    throw new CuartelesLoadError('La lista de cuarteles no es un objeto JSON.')
  }
  const raw = payload as Record<string, unknown>
  if (raw['type'] !== 'FeatureCollection' || !Array.isArray(raw['features'])) {
    throw new CuartelesLoadError('La lista de cuarteles no es un FeatureCollection.')
  }

  const cuarteles: Cuartel[] = []
  const features: CuartelFeature[] = []
  for (const [index, item] of (raw['features'] as unknown[]).entries()) {
    if (typeof item !== 'object' || item === null) continue
    const feature = item as Record<string, unknown>
    const geometry = feature['geometry'] as Record<string, unknown> | undefined
    const coords = geometry?.['coordinates']
    const props = (feature['properties'] ?? {}) as Record<string, unknown>
    if (!Array.isArray(coords) || coords.length !== 2) continue
    const [lon, lat] = coords as unknown[]
    if (typeof lon !== 'number' || typeof lat !== 'number') continue
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue
    const nombre = texto(props['nombre'])
    const cuerpo = texto(props['cuerpo'])
    if (!nombre || !cuerpo) continue
    const tipo: CuartelTipo = props['tipo'] === 'cuerpo' ? 'cuerpo' : 'compania'
    const numero = typeof props['numero'] === 'number' ? props['numero'] : null
    const cuartel: Cuartel = {
      id: `cuartel-${index}`,
      tipo,
      nombre,
      cuerpo,
      numero,
      direccion: texto(props['direccion']),
      comuna: texto(props['comuna']),
      lon,
      lat,
    }
    cuarteles.push(cuartel)
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [lon, lat] },
      properties: {
        id: cuartel.id,
        tipo,
        nombre,
        cuerpo,
        numero,
        direccion: cuartel.direccion,
        comuna: cuartel.comuna,
      },
    })
  }

  if (cuarteles.length === 0) {
    throw new CuartelesLoadError('La lista de cuarteles llegó vacía.')
  }

  const metadata = (raw['metadata'] ?? {}) as Record<string, unknown>
  return {
    collection: { type: 'FeatureCollection', features },
    cuarteles,
    fuente: texto(metadata['fuente']) ?? 'SIG Bomberos de Chile',
    generado: texto(metadata['generado']),
  }
}

export async function fetchCuarteles(signal?: AbortSignal): Promise<CuartelesData> {
  let response: Response
  try {
    // `default` y no `force-cache`: el endpoint responde con `ETag` y una
    // instantánea regenerada tiene que llegar (ver `api/hazard.ts`).
    response = await fetch(CUARTELES_SOURCE_URL, { signal: signal ?? null, cache: 'default' })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new CuartelesLoadError('No se pudo contactar al servidor.')
  }
  if (!response.ok) {
    throw new CuartelesLoadError(`El servidor respondió ${response.status}.`)
  }
  return parseCuarteles(await response.json())
}
