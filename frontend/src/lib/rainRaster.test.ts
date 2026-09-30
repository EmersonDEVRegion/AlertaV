import { describe, expect, it } from 'vitest'
import type { RainGrid } from '@/api/rainGrid'
import { edgeWobble, gridCorners, rainPixels, sampleGrid } from './rainRaster'

const GRID: RainGrid = {
  generatedAt: 0,
  hours: 24,
  step: 0.5,
  west: -72,
  north: -32,
  nx: 3,
  ny: 2,
  // Fila norte: seco, 2, 4. Fila sur: seco, sin dato, 8.
  values: [0, 2, 4, 0, null, 8],
}

describe('campo de lluvia interpolado', () => {
  it('las esquinas van de noroeste a suroeste', () => {
    expect(gridCorners(GRID)).toEqual([
      [-72, -32],
      [-71, -32],
      [-71, -32.5],
      [-72, -32.5],
    ])
  })

  it('en un nodo devuelve su valor exacto', () => {
    expect(sampleGrid(GRID, 1, 0)).toBe(2)
    expect(sampleGrid(GRID, 2, 1)).toBe(8)
  })

  it('entre dos nodos interpola: el color es un valor, no una suma', () => {
    expect(sampleGrid(GRID, 1.5, 0)).toBeCloseTo(3)
    expect(sampleGrid(GRID, 2, 0.5)).toBeCloseTo(6)
  })

  it('una celda sin dato cuenta como seca, pero sin ningún dato no se inventa', () => {
    expect(sampleGrid(GRID, 1, 1)).toBe(0)
    const empty: RainGrid = { ...GRID, values: [null, null, null, null, null, null] }
    expect(sampleGrid(empty, 0.5, 0.5)).toBeNull()
  })

  it('lo seco queda transparente y la lluvia fuerte opaca', () => {
    const pixels = rainPixels(GRID, 3, 2, 0, 0)
    const alpha = (x: number, y: number) => pixels[(y * 3 + x) * 4 + 3]!
    expect(alpha(0, 0)).toBe(0)
    expect(alpha(2, 1)).toBeGreaterThan(alpha(1, 0))
  })

  it('el borde de la grilla se desvanece: el modelo no termina en una línea recta', () => {
    const wet: RainGrid = { ...GRID, nx: 5, ny: 5, values: Array(25).fill(8) }
    const pixels = rainPixels(wet, 5, 5, 1.5, 0)
    const alpha = (x: number, y: number) => pixels[(y * 5 + x) * 4 + 3]!
    expect(alpha(0, 2)).toBe(0)
    expect(alpha(2, 2)).toBeGreaterThan(alpha(1, 2))
    expect(alpha(1, 2)).toBeGreaterThan(0)
  })

  it('el final del difuminado ondula: a la misma distancia del borde el alfa no es constante', () => {
    // §L: un degradado recto se seguía leyendo como una recta sobre la cordillera.
    const n = 41
    const wet: RainGrid = { ...GRID, nx: n, ny: n, values: Array(n * n).fill(8) }
    const pixels = rainPixels(wet, n, n)
    const alpha = (x: number, y: number) => pixels[(y * n + x) * 4 + 3]!
    const column = Array.from({ length: n - 10 }, (_, i) => alpha(2, i + 5))
    expect(new Set(column).size).toBeGreaterThan(3)
    // En el borde mismo sigue siendo transparente, ondule como ondule.
    for (let y = 0; y < n; y += 1) expect(alpha(0, y)).toBe(0)
    // Y lejos del borde la lluvia llega entera.
    expect(alpha(20, 20)).toBe(alpha(20, 21))
  })

  it('la ondulación es determinista y está en [0, 1]', () => {
    for (let i = 0; i < 50; i += 1) {
      const w = edgeWobble(i * 0.37, i * 0.91)
      expect(w).toBeGreaterThanOrEqual(0)
      expect(w).toBeLessThanOrEqual(1)
      expect(edgeWobble(i * 0.37, i * 0.91)).toBe(w)
    }
  })
})
