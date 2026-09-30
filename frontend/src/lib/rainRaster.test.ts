import { describe, expect, it } from 'vitest'
import type { RainGrid } from '@/api/rainGrid'
import { gridCorners, rainPixels, sampleGrid } from './rainRaster'

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
    const pixels = rainPixels(GRID, 3, 2, 0)
    const alpha = (x: number, y: number) => pixels[(y * 3 + x) * 4 + 3]!
    expect(alpha(0, 0)).toBe(0)
    expect(alpha(2, 1)).toBeGreaterThan(alpha(1, 0))
  })

  it('el borde de la grilla se desvanece: el modelo no termina en una línea recta', () => {
    const wet: RainGrid = { ...GRID, nx: 5, ny: 5, values: Array(25).fill(8) }
    const pixels = rainPixels(wet, 5, 5, 1.5)
    const alpha = (x: number, y: number) => pixels[(y * 5 + x) * 4 + 3]!
    expect(alpha(0, 2)).toBe(0)
    expect(alpha(2, 2)).toBeGreaterThan(alpha(1, 2))
    expect(alpha(1, 2)).toBeGreaterThan(0)
  })
})
