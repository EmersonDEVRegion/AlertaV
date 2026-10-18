// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { REGION, REGION_BBOX, SEISMIC_BBOX, nearRegion, regionMask, signedArea } from './region'

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

describe('máscara', () => {
  it('el exterior gira al revés que el agujero (así MapLibre lo trata como agujero)', () => {
    const [outer = [], ...holes] = regionMask().geometry.coordinates
    expect(signedArea(outer)).toBeGreaterThan(0)
    expect(holes.length).toBeGreaterThan(0)
    for (const hole of holes) expect(signedArea(hole)).toBeLessThan(0)
  })

  it('el agujero es la región y su mar, no la tierra de otra región', () => {
    const [outer = [], hole = []] = regionMask().geometry.coordinates
    expect(inside(outer, -71.62, -33.05)).toBe(true)
    expect(inside(hole, -71.62, -33.05)).toBe(true) // Valparaíso
    expect(inside(hole, -71.75, -33.03)).toBe(true) // el mar frente a Valparaíso
    expect(inside(hole, -71.95, -33.4)).toBe(true) // la franja de mar
    expect(inside(hole, -72.6, -33.4)).toBe(false) // mar adentro: tapado
    expect(inside(hole, -71.16, -31.63)).toBe(false) // Illapel
    expect(inside(hole, -70.65, -33.45)).toBe(false) // Santiago
    expect(inside(hole, -72.0, -34.4)).toBe(false) // mar de O'Higgins
    expect(inside(hole, -71.6, -31.9)).toBe(false) // mar de Coquimbo
  })

  it('el exterior cubre todo lo que el mapa deja mirar', () => {
    const [outer = []] = regionMask().geometry.coordinates
    for (const [lon = 0, lat = 0] of [
      [SEISMIC_BBOX[0] - 2, SEISMIC_BBOX[1] - 2],
      [SEISMIC_BBOX[2] + 2, SEISMIC_BBOX[3] + 2],
    ]) {
      expect(inside(outer, lon, lat)).toBe(true)
    }
  })

  it('signedArea: antihorario positivo', () => {
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
