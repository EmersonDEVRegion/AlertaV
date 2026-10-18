/**
 * La Región de Valparaíso continental: su contorno y lo que se arma con él.
 *
 * El contorno es la unión de las 36 comunas (`backend/scripts/region_contorno.py`
 * sobre `comunas_v_region.geojson`, las mismas de la tabla `comunas_region`),
 * simplificada a ~150 m. El mismo archivo trae lo que la máscara deja ver. Va en el bundle: la máscara tiene que estar desde el
 * primer cuadro, también sin conexión.
 */

import type { Feature, FeatureCollection, MultiPolygon, Polygon, Position } from 'geojson'
import regionFile from '@/data/region_valpo.json'

export type Bbox = readonly [west: number, south: number, east: number, north: number]

type Area = Feature<Polygon | MultiPolygon, { tipo: 'region' | 'visible' }>
const AREAS = (regionFile as unknown as FeatureCollection<Polygon | MultiPolygon>).features as Area[]

function area(tipo: Area['properties']['tipo']): Area {
  const found = AREAS.find((f) => f.properties.tipo === tipo)
  if (!found) throw new Error(`region_valpo.json no trae «${tipo}»`)
  return found
}

/** El contorno de la región: el borde dibujado y «¿está en la región?». */
export const REGION = area('region')

/**
 * Lo que la máscara deja sin tapar: la región más el mar de enfrente. Sin el
 * mar, la máscara cortaría los rótulos de Valparaíso, San Antonio o Quintero,
 * que el mapa base dibuja sobre el agua.
 */
const VISIBLE = area('visible')

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
 * por tierra, ~90 km más allá de la región. Fuera de la región todo queda
 * bajo la máscara: se puede llegar, pero sólo se ve el sismo.
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

function withWinding(ring: Position[], counterClockwise: boolean): Position[] {
  return signedArea(ring) > 0 === counterClockwise ? ring : [...ring].reverse()
}

/**
 * Rectángulo mucho más grande que todo lo que el mapa deja mirar. No hace
 * falta que sea el mundo: con eso basta para que nunca se vea su borde.
 */
const OUTSIDE: Position[] = [
  [-90, -60],
  [-50, -60],
  [-50, -10],
  [-90, -10],
  [-90, -60],
]

/**
 * «Todo menos la región y su mar»: un polígono con `VISIBLE` como agujero.
 *
 * MapLibre decide qué anillo es agujero por el sentido en que gira (el
 * opuesto al exterior), no por su posición en el arreglo: el exterior va
 * antihorario y el agujero horario, pase lo que pase con el archivo.
 */
export function regionMask(): Feature<Polygon> {
  const holes = exteriorRings(VISIBLE.geometry).map((ring) => withWinding(ring, false))
  return {
    type: 'Feature',
    properties: {},
    geometry: { type: 'Polygon', coordinates: [withWinding(OUTSIDE, true), ...holes] },
  }
}
