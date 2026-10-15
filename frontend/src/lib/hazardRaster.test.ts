// @vitest-environment node
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import type { HazardCells } from '@/api/hazardTypes'
import { HAZARD_RAMP } from '@/domain/hazardSymbology'
import {
  buildHazardLattice,
  hazardColor,
  hazardCorners,
  hazardImageSize,
  hazardPixels,
  labToRgb,
  rgbToLab,
  sampleHazard,
} from './hazardRaster'

/**
 * Una grilla como la del CSN, en chico: filas a paso fijo en latitud, pero
 * cada una con su propio origen en longitud (la deriva de la proyección
 * métrica). El valor sube de oeste a este: `0,3 + 0,05 · columna`.
 */
function synthetic(rows = 12, cols = 14): HazardCells {
  const features: HazardCells['features'] = []
  for (let r = 0; r < rows; r += 1) {
    const lat = -33.8 + r * 0.045
    const lon0 = -72 - r * 0.002
    for (let c = 0; c < cols; c += 1) {
      const lon = lon0 + c * 0.054
      features.push({
        type: 'Feature',
        geometry: { type: 'Polygon', coordinates: [[[lon, lat]]] },
        properties: { lon, lat, pga_475: 0.3 + 0.05 * c },
      })
    }
  }
  return { type: 'FeatureCollection', features }
}

const hexRgb = (c: string) => {
  const v = c.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)) as [number, number, number]
}

const hex = (rgb: [number, number, number]) =>
  '#' + rgb.map((v) => v.toString(16).padStart(2, '0')).join('')

describe('la grilla de nodos', () => {
  it('guarda cada fila con su propio origen: la grilla del CSN deriva en longitud', () => {
    const lattice = buildHazardLattice(synthetic())!
    expect(lattice.rows).toHaveLength(12)
    expect(lattice.rows[0]!.lon0).toBeCloseTo(-72)
    expect(lattice.rows[5]!.lon0).toBeCloseTo(-72.01)
    expect(lattice.rows[0]!.step).toBeCloseTo(0.054)
  })

  it('en un nodo devuelve su valor exacto, aunque la fila esté corrida', () => {
    const lattice = buildHazardLattice(synthetic())!
    const row = lattice.rows[5]!
    const s = sampleHazard(lattice, row.lon0 + 4 * row.step, row.lat)
    expect(s.value).toBeCloseTo(0.5)
  })

  it('entre nodos interpola: el color es un valor del modelo, no una suma', () => {
    const lattice = buildHazardLattice(synthetic())!
    const row = lattice.rows[5]!
    const s = sampleHazard(lattice, row.lon0 + 4.5 * row.step, row.lat)
    expect(s.value).toBeCloseTo(0.525)
  })

  it('mide la distancia al borde en celdas, negativa afuera', () => {
    const lattice = buildHazardLattice(synthetic())!
    // Fila 6 de 12 (a 5 del borde norte), columna 6 de 14: manda la fila.
    const row = lattice.rows[6]!
    expect(sampleHazard(lattice, row.lon0 + 6 * row.step, row.lat).edge).toBeCloseTo(5)
    expect(sampleHazard(lattice, row.lon0 + 2 * row.step, row.lat).edge).toBeCloseTo(2)
    expect(sampleHazard(lattice, row.lon0 - row.step, row.lat).edge).toBeLessThan(0)
  })

  it('sin al menos dos filas no hay grilla', () => {
    expect(buildHazardLattice(synthetic(1))).toBeNull()
  })
})

describe('la imagen', () => {
  const lattice = buildHazardLattice(synthetic(24, 26))!
  const { width, height } = hazardImageSize(lattice)
  const pixels = hazardPixels(lattice, 'dark', width, height)
  const alpha = (x: number, y: number) => pixels[(y * width + x) * 4 + 3]!

  it('las esquinas van de noroeste a suroeste y cubren los nodos extremos', () => {
    const [nw, ne, se, sw] = hazardCorners(lattice)
    expect(nw).toEqual([lattice.west, lattice.north])
    expect(ne[0]).toBeGreaterThan(nw[0])
    expect(se[1]).toBeLessThan(ne[1])
    expect(sw).toEqual([lattice.west, lattice.south])
  })

  it('el borde es transparente: la caja de la grilla no se dibuja como una línea', () => {
    for (let x = 0; x < width; x += 1) {
      expect(alpha(x, 0)).toBe(0)
      expect(alpha(x, height - 1)).toBe(0)
    }
    for (let y = 0; y < height; y += 1) {
      expect(alpha(0, y)).toBe(0)
      expect(alpha(width - 1, y)).toBe(0)
    }
  })

  it('el centro llega entero', () => {
    expect(alpha(Math.floor(width / 2), Math.floor(height / 2))).toBe(255)
  })

  it('el final del difuminado ondula: a la misma distancia del borde el alfa cambia', () => {
    // Tres celdas adentro del borde oeste, a lo largo de toda la altura.
    const x = Math.round((width / (lattice.east - lattice.west)) * lattice.rows[0]!.step * 2)
    const column = Array.from({ length: height - 40 }, (_, i) => alpha(x, i + 20))
    expect(new Set(column).size).toBeGreaterThan(3)
  })

  it('la imagen es determinista', () => {
    expect(hazardPixels(lattice, 'dark', width, height)).toEqual(pixels)
  })

  it('los píxeles son casi cuadrados en pantalla (filas en Mercator)', () => {
    const lonSpan = lattice.east - lattice.west
    const latSpan = lattice.north - lattice.south
    // A −33° un grado de latitud ocupa en Mercator ~1,2 veces un grado de longitud.
    const ratio = height / width / (latSpan / lonSpan)
    expect(ratio).toBeGreaterThan(1.15)
    expect(ratio).toBeLessThan(1.25)
  })
})

describe('el color', () => {
  it('Lab ida y vuelta conserva el color (±1 por redondeo)', () => {
    for (const [, c] of [...HAZARD_RAMP.dark.stops, ...HAZARD_RAMP.light.stops]) {
      const v = c.replace('#', '')
      const rgb = [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)) as [number, number, number]
      const back = labToRgb(rgbToLab(rgb))
      back.forEach((channel, i) => expect(Math.abs(channel - rgb[i]!)).toBeLessThanOrEqual(1))
    }
  })

  it('entre dos paradas mezcla en Lab: el punto medio no se hunde como en sRGB', () => {
    const [[v0, c0], [v1, c1]] = HAZARD_RAMP.dark.stops as [[number, string], [number, string]]
    const lab0 = rgbToLab(hexRgb(c0))
    const lab1 = rgbToLab(hexRgb(c1))
    const mid = rgbToLab(hazardColor('dark', (v0 + v1) / 2))
    expect(mid[0]).toBeCloseTo((lab0[0] + lab1[0]) / 2, 0)
  })

  it('en cada parada de la rampa da exactamente ese color', () => {
    for (const theme of ['light', 'dark'] as const) {
      for (const [value, color] of HAZARD_RAMP[theme].stops) {
        expect(hex(hazardColor(theme, value))).toBe(color)
      }
    }
  })

  it('satura fuera de rango: nada desaparece del mapa por caer fuera de la rampa', () => {
    const stops = HAZARD_RAMP.dark.stops
    expect(hex(hazardColor('dark', 0))).toBe(stops[0]![1])
    expect(hex(hazardColor('dark', 5))).toBe(stops[stops.length - 1]![1])
  })
})

const ARTIFACT = fileURLToPath(
  new URL('../../../backend/static/geo/amenaza_sismica_valpo.json', import.meta.url),
)

describe.skipIf(!existsSync(ARTIFACT))('con el artefacto real del CSN', () => {
  const cells = JSON.parse(readFileSync(ARTIFACT, 'utf8')) as HazardCells
  const lattice = buildHazardLattice(cells)!

  it('reconoce las 40 filas y ningún nodo queda fuera', () => {
    expect(lattice.rows).toHaveLength(40)
    const nodes = lattice.rows.reduce(
      (n, row) => n + row.values.filter((v) => !Number.isNaN(v)).length,
      0,
    )
    expect(nodes).toBe(cells.features.length)
  })

  it('cada nodo, muestreado en su centro, devuelve su PGA', () => {
    for (const feature of cells.features.filter((_, i) => i % 97 === 0)) {
      const { lon, lat, pga_475 } = feature.properties
      expect(sampleHazard(lattice, lon, lat).value).toBeCloseTo(pga_475, 3)
    }
  })

  it('Valparaíso queda lejos del borde difuminado', () => {
    // −71,62 / −33,05: la costa, la zona de mayor amenaza. Tiene que llegar entera.
    expect(sampleHazard(lattice, -71.62, -33.05).edge).toBeGreaterThan(4)
  })
})
