// @vitest-environment node
/**
 * La grilla de lluvia del backend cubre exactamente lo que el mapa deja mirar
 * (§L): así el único borde del campo queda en el límite del mapa. Si alguien
 * mueve `MAP_MAX_BOUNDS` sin mover `RAIN_GRID_*` en `backend/app/core/config.py`,
 * el borde vuelve a verse como una recta. El backend comprueba el tamaño de la
 * grilla y el presupuesto de Open-Meteo (`tests/test_rain_grid.py`).
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { MAP_MAX_BOUNDS } from './map'

const config = readFileSync(resolve(process.cwd(), '../backend/app/core/config.py'), 'utf8')

function setting(name: string): number {
  const match = config.match(new RegExp(`^\\s*${name}: float = (-?[\\d.]+)`, 'm'))
  if (!match) throw new Error(`${name} no está en config.py`)
  return Number(match[1])
}

describe('grilla de lluvia y límites del mapa', () => {
  it('la caja de RAIN_GRID_* es MAP_MAX_BOUNDS', () => {
    const [west, south, east, north] = MAP_MAX_BOUNDS
    expect(setting('RAIN_GRID_WEST')).toBe(west)
    expect(setting('RAIN_GRID_SOUTH')).toBe(south)
    expect(setting('RAIN_GRID_EAST')).toBe(east)
    expect(setting('RAIN_GRID_NORTH')).toBe(north)
  })
})
