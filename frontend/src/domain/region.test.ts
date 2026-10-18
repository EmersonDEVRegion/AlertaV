// @vitest-environment node
import { describe, expect, it } from 'vitest'
import {
  REGION,
  REGION_BBOX,
  REGION_VIGNETTE,
  SEISMIC_BBOX,
  nearRegion,
  signedArea,
} from './region'

/** Punto en anillo (rayo hacia el este). */
function inside(ring: readonly number[][], lon: number, lat: number): boolean {
  let hit = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [xi = 0, yi = 0] = ring[i]!
    const [xj = 0, yj = 0] = ring[j]!
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) hit = !hit
  }
  return hit
}

const region = () => {
  const g = REGION.geometry
  return (g.type === 'Polygon' ? g.coordinates[0] : g.coordinates[0]?.[0]) ?? []
}

describe('contorno de la región', () => {
  it('cubre las 36 comunas continentales, de Petorca a Santo Domingo', () => {
    const [west, south, east, north] = REGION_BBOX
    expect(west).toBeCloseTo(-71.84, 1)
    expect(east).toBeCloseTo(-69.99, 1)
    expect(south).toBeLessThan(-33.9) // Santo Domingo
    expect(north).toBeGreaterThan(-32.1) // Petorca
  })

  it('deja dentro las ciudades y fuera a los vecinos', () => {
    const ring = region()
    expect(inside(ring, -71.62, -33.05)).toBe(true) // Valparaíso
    expect(inside(ring, -70.6, -32.83)).toBe(true) // Los Andes
    expect(inside(ring, -71.61, -33.59)).toBe(true) // San Antonio
    expect(inside(ring, -71.16, -31.63)).toBe(false) // Illapel
    expect(inside(ring, -70.99, -34.58)).toBe(false) // San Fernando
    expect(inside(ring, -70.65, -33.45)).toBe(false) // Santiago
    expect(inside(ring, -68.84, -32.89)).toBe(false) // Mendoza
  })

  it('la caja de sismos contiene la región y llega al mar', () => {
    const [west, south, east, north] = SEISMIC_BBOX
    expect(west).toBeLessThan(REGION_BBOX[0] - 1)
    expect(south).toBeLessThan(REGION_BBOX[1])
    expect(east).toBeGreaterThan(REGION_BBOX[2])
    expect(north).toBeGreaterThan(REGION_BBOX[3])
  })
})

/** Las capas de la viñeta cuyo agujero NO contiene el punto: las que lo cubren. */
function coveringLayers(lon: number, lat: number) {
  return REGION_VIGNETTE.features.filter((f) => {
    const [, ...holes] = f.geometry.coordinates
    return !holes.some((hole) => inside(hole, lon, lat))
  })
}

describe('viñeta', () => {
  const layers = REGION_VIGNETTE.features

  it('de 3 en 3 km hasta 40, cada capa encadenada con la anterior', () => {
    expect(layers.length).toBeGreaterThanOrEqual(10)
    expect(layers.at(-1)?.properties.km).toBe(40)
    expect(layers.at(-1)?.properties.f).toBe(1)
    layers.forEach((layer, i) => {
      const prev = i === 0 ? 0 : layers[i - 1]!.properties.f
      expect(layer.properties.fp).toBe(prev)
      expect(layer.properties.f).toBeGreaterThan(prev)
      expect(layer.properties.f - prev).toBeLessThan(0.15) // escalones que no se ven
    })
  })

  it('el exterior gira al revés que los agujeros (así MapLibre los trata como agujeros)', () => {
    for (const layer of layers) {
      const [outer = [], ...holes] = layer.geometry.coordinates
      expect(signedArea(outer)).toBeGreaterThan(0)
      expect(holes.length).toBeGreaterThan(0)
      for (const hole of holes) expect(signedArea(hole)).toBeLessThan(0)
    }
  })

  it('el exterior cubre todo lo que el mapa deja mirar', () => {
    for (const layer of layers) {
      const [outer = []] = layer.geometry.coordinates
      expect(inside(outer, SEISMIC_BBOX[0] - 2, SEISMIC_BBOX[1] - 2)).toBe(true)
      expect(inside(outer, SEISMIC_BBOX[2] + 2, SEISMIC_BBOX[3] + 2)).toBe(true)
    }
  })

  it('dentro de la región nada; lo vecino a medias; lo lejano al tope', () => {
    const darkness = (lon: number, lat: number) =>
      coveringLayers(lon, lat).at(-1)?.properties.f ?? 0
    expect(darkness(-71.62, -33.05)).toBe(0) // Valparaíso
    expect(darkness(-70.6, -32.83)).toBe(0) // Los Andes
    const colina = darkness(-70.67, -33.2) // ~15 km del borde
    expect(colina).toBeGreaterThan(0.1)
    expect(colina).toBeLessThan(0.7)
    expect(darkness(-71.75, -33.05)).toBeLessThan(0.15) // el mar frente a Valparaíso
    expect(darkness(-71.16, -31.63)).toBe(1) // Illapel
    expect(darkness(-70.99, -34.58)).toBe(1) // San Fernando
    expect(darkness(-68.84, -32.89)).toBe(1) // Mendoza
  })

  it('las capas que cubren un punto son siempre las primeras (se apilan en orden)', () => {
    const covering = coveringLayers(-70.67, -33.2)
    expect(covering).toEqual(layers.slice(0, covering.length))
  })
})

describe('signedArea', () => {
  it('antihorario positivo', () => {
    const ccw = [
      [0, 0],
      [1, 0],
      [1, 1],
      [0, 1],
      [0, 0],
    ]
    expect(signedArea(ccw)).toBeCloseTo(1)
    expect(signedArea([...ccw].reverse())).toBeCloseTo(-1)
  })
})

describe('nearRegion', () => {
  it('dentro, en la orilla y fuera', () => {
    expect(nearRegion(-33.05, -71.62)).toBe(true) // Valparaíso
    expect(nearRegion(-32.95, -71.555)).toBe(true) // Reñaca, en la playa
    expect(nearRegion(-33.94, -71.73)).toBe(true) // sur de Santo Domingo
    expect(nearRegion(-33.45, -70.65)).toBe(false) // Santiago, dentro de la caja
    expect(nearRegion(-34.58, -70.99)).toBe(false) // San Fernando
    expect(nearRegion(-31.63, -71.16)).toBe(false) // Illapel
  })
})
