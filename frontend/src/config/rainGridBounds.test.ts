// @vitest-environment node
/**
 * Las cajas del backend calzan con la región que dibuja la PWA.
 *
 * - **Lluvia** (`RAIN_GRID_*`): la PWA tapa lo que queda fuera de la región, así
 *   que la grilla tiene que cubrir la región con margen suficiente para que el
 *   borde difuminado de la imagen (hasta 3,4 celdas, `lib/rainRaster.ts`) quede
 *   bajo la máscara. El backend comprueba el tamaño de la grilla y el
 *   presupuesto de Open-Meteo (`tests/test_rain_grid.py`).
 * - **Sismos** (`USGS_*`, `CSN_*`): la cámara puede llegar hasta `SEISMIC_BBOX`
 *   para mostrar un sismo; si el backend guardara uno más lejos, la cámara se
 *   detendría antes de verlo.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { REGION_BBOX, SEISMIC_BBOX } from '@/domain/region'

const config = readFileSync(resolve(process.cwd(), '../backend/app/core/config.py'), 'utf8')

function setting(name: string): number {
  const match = config.match(new RegExp(`^\\s*${name}: float = (-?[\\d.]+)`, 'm'))
  if (!match) throw new Error(`${name} no está en config.py`)
  return Number(match[1])
}

function box(prefix: string): [number, number, number, number] {
  return [
    setting(`${prefix}_WEST`),
    setting(`${prefix}_SOUTH`),
    setting(`${prefix}_EAST`),
    setting(`${prefix}_NORTH`),
  ]
}

describe('cajas del backend y región de la PWA', () => {
  it('la grilla de lluvia cubre la región con el difuminado fuera', () => {
    const step = Number(config.match(/RAIN_GRID_STEP_DEGREES: float = Field\(default=([\d.]+)/)?.[1])
    const margin = 3.4 * step
    const [west, south, east, north] = box('RAIN_GRID')
    const [rWest, rSouth, rEast, rNorth] = REGION_BBOX
    expect(west).toBeLessThanOrEqual(rWest - margin)
    expect(south).toBeLessThanOrEqual(rSouth - margin)
    expect(east).toBeGreaterThanOrEqual(rEast + margin)
    expect(north).toBeGreaterThanOrEqual(rNorth + margin)
  })

  it('la caja de sismos del USGS y del CSN es SEISMIC_BBOX', () => {
    expect(box('USGS')).toEqual([...SEISMIC_BBOX])
    expect(box('CSN')).toEqual([...SEISMIC_BBOX])
  })

  it('la caja regional del backend contiene el contorno', () => {
    const [west, south, east, north] = box('REGION')
    expect(west).toBeLessThanOrEqual(REGION_BBOX[0])
    expect(south).toBeLessThanOrEqual(REGION_BBOX[1])
    expect(east).toBeGreaterThanOrEqual(REGION_BBOX[2])
    expect(north).toBeGreaterThanOrEqual(REGION_BBOX[3])
  })
})
