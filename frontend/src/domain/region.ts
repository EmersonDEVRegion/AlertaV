/**
 * La Región de Valparaíso continental: su contorno y lo que se arma con él.
 *
 * El contorno es la unión de las 36 comunas (`backend/scripts/region_contorno.py`
 * sobre `comunas_v_region.geojson`, las mismas de la tabla `comunas_region`),
 * simplificada a ~150 m. El mismo archivo trae la viñeta que apaga lo de
 * afuera. Va en el bundle: tiene que estar desde el primer cuadro, también sin
 * conexión.
 */

import type { Feature, FeatureCollection, MultiPolygon, Polygon, Position } from 'geojson'
import regionFile from '@/data/region_valpo.json'

export type Bbox = readonly [west: number, south: number, east: number, north: number]

/**
 * Una capa de la viñeta: un rectángulo enorme con la región engordada `km`
 * kilómetros como agujero. `f` es la oscuridad buscada más allá de `km` (de 0
 * a 1) y `fp` la de la capa anterior: con eso cada capa sabe cuánto pintar
 * para que el apilado dé `f` (ver `regionMaskLayers.ts`).
 */
export interface VignetteProps {
  tipo: 'vineta'
  km: number
  f: number
  fp: number
}

type RegionFeature = Feature<Polygon | MultiPolygon, { tipo: 'region' }>
type VignetteFeature = Feature<Polygon, VignetteProps>

const FEATURES = (regionFile as unknown as FeatureCollection).features as (
  | RegionFeature
  | VignetteFeature
)[]

function regionFeature(): RegionFeature {
  const found = FEATURES.find((f): f is RegionFeature => f.properties.tipo === 'region')
  if (!found) throw new Error('region_valpo.json no trae el contorno de la región')
  return found
}

/** El contorno de la región: el borde dibujado y «¿está en la región?». */
export const REGION = regionFeature()

/**
 * La viñeta: transparente en el borde de la región y cada vez más oscura hasta
 * ~40 km, donde llega al tope del tema. Se ve la región como iluminada y lo
 * vecino (Los Vilos, Tiltil, Colina) como contexto apagado; el mar recibe el
 * mismo degradado. Del agujero más chico al más grande.
 */
export const REGION_VIGNETTE: FeatureCollection<Polygon, VignetteProps> = {
  type: 'FeatureCollection',
  features: FEATURES.filter((f): f is VignetteFeature => f.properties.tipo === 'vineta'),
}

/** Los anillos exteriores del contorno (uno por polígono). */
function exteriorRings(geometry: Polygon | MultiPolygon): Position[][] {
  return geometry.type === 'Polygon'
    ? [geometry.coordinates[0] ?? []]
    : geometry.coordinates.map((polygon) => polygon[0] ?? [])
}

function bboxOf(rings: readonly Position[][]): Bbox {
  let west = Infinity
  let south = Infinity
  let east = -Infinity
  let north = -Infinity
  for (const ring of rings) {
    for (const [lon = NaN, lat = NaN] of ring) {
      west = Math.min(west, lon)
      east = Math.max(east, lon)
      south = Math.min(south, lat)
      north = Math.max(north, lat)
    }
  }
  return [west, south, east, north]
}

/** Caja del contorno: ~[−71,84, −33,96, −69,99, −32,02]. */
export const REGION_BBOX: Bbox = bboxOf(exteriorRings(REGION.geometry))

/**
 * Hasta dónde puede ir la cámara para mostrar un sismo: la caja de sismos del
 * backend (`USGS_*` y `CSN_*` en `backend/app/core/config.py`, lo vigila
 * `config/rainGridBounds.test.ts`). Llega a −73 por el mar, donde está la fosa;
 * por tierra, ~90 km más allá de la región. Ahí la viñeta ya está al tope: se
 * puede llegar, pero se ve sobre todo el sismo.
 */
export const SEISMIC_BBOX: Bbox = [-73.0, -34.8, -69.2, -31.2]

/** Punto en anillo (rayo hacia el este). */
function inRing(ring: readonly Position[], lon: number, lat: number): boolean {
  let hit = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [xi = 0, yi = 0] = ring[i]!
    const [xj = 0, yj = 0] = ring[j]!
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) hit = !hit
  }
  return hit
}

/** Tolerancia de `nearRegion`, en grados (~5 km): la playa, el borde simplificado. */
const NEAR_DEG = 0.05

/**
 * ¿El punto está en la región o a ~5 km de su borde?
 *
 * Con el contorno y no con la caja: la caja de la región incluye Santiago.
 */
export function nearRegion(lat: number, lon: number): boolean {
  const [west, south, east, north] = REGION_BBOX
  if (lon < west - NEAR_DEG || lon > east + NEAR_DEG) return false
  if (lat < south - NEAR_DEG || lat > north + NEAR_DEG) return false
  const rings = exteriorRings(REGION.geometry)
  if (rings.some((ring) => inRing(ring, lon, lat))) return true
  // Cerca de algún vértice del borde (cada ~150 m a 1 km).
  const cos = Math.cos((lat * Math.PI) / 180)
  return rings.some((ring) =>
    ring.some(([x = NaN, y = NaN]) => Math.hypot((x - lon) * cos, y - lat) < NEAR_DEG),
  )
}

/** Área con signo (fórmula del cordón): positiva = antihoraria. */
export function signedArea(ring: readonly Position[]): number {
  let sum = 0
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [xi = 0, yi = 0] = ring[i]!
    const [xj = 0, yj = 0] = ring[j]!
    sum += (xj - xi) * (yj + yi)
  }
  return sum / 2
}
