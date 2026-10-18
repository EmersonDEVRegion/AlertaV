// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { REGION_BBOX, SEISMIC_BBOX } from '@/domain/region'
import { regionCamera } from './regionCamera'

const NONE = { top: 0, right: 0, bottom: 0, left: 0 }
const PHONE = { width: 430, height: 932 }
const PHONE_INSETS = { top: 64, right: 0, bottom: 116, left: 0 }
const DESKTOP = { width: 1440, height: 900 }
const DESKTOP_INSETS = { top: 48, right: 0, bottom: 0, left: 364 }

/** Píxeles por grado de longitud a un zoom (teselas de 512). */
const pxPerLon = (zoom: number) => (512 * 2 ** zoom) / 360

describe('regionCamera', () => {
  it('en el teléfono, al zoom mínimo la región cabe de lado a lado', () => {
    const { minZoom } = regionCamera(PHONE, PHONE_INSETS)
    const regionWidthPx = (REGION_BBOX[2] - REGION_BBOX[0]) * pxPerLon(minZoom)
    expect(regionWidthPx).toBeLessThanOrEqual(PHONE.width)
    expect(regionWidthPx).toBeGreaterThan(PHONE.width * 0.75) // y la llena
  })

  it('en el escritorio, la región cabe a la derecha de la columna', () => {
    const { minZoom } = regionCamera(DESKTOP, DESKTOP_INSETS)
    const regionWidthPx = (REGION_BBOX[2] - REGION_BBOX[0]) * pxPerLon(minZoom)
    expect(regionWidthPx).toBeLessThanOrEqual(DESKTOP.width - DESKTOP_INSETS.left)
  })

  it('al zoom mínimo no se ve Illapel ni San Fernando en el escritorio', () => {
    const { center, minZoom } = regionCamera(DESKTOP, DESKTOP_INSETS)
    // Alto visible en grados de latitud (aprox. a la latitud de la región).
    const latSpan = DESKTOP.height / pxPerLon(minZoom) * Math.cos((33 * Math.PI) / 180)
    expect(center.latitude + latSpan / 2).toBeLessThan(-31.63)
    expect(center.latitude - latSpan / 2).toBeGreaterThan(-34.58)
  })

  it('los límites contienen la región y la caja de sismos', () => {
    for (const [viewport, insets] of [
      [PHONE, PHONE_INSETS],
      [DESKTOP, DESKTOP_INSETS],
      [{ width: 1920, height: 1080 }, NONE],
    ] as const) {
      const [west, south, east, north] = regionCamera(viewport, insets).maxBounds
      expect(west).toBeLessThanOrEqual(Math.min(REGION_BBOX[0], SEISMIC_BBOX[0]))
      expect(south).toBeLessThanOrEqual(Math.min(REGION_BBOX[1], SEISMIC_BBOX[1]))
      expect(east).toBeGreaterThanOrEqual(Math.max(REGION_BBOX[2], SEISMIC_BBOX[2]))
      expect(north).toBeGreaterThanOrEqual(Math.max(REGION_BBOX[3], SEISMIC_BBOX[3]))
    }
  })

  it('la vista inicial deja la región entera en lo que no está tapado', () => {
    for (const [viewport, insets] of [
      [PHONE, PHONE_INSETS],
      [DESKTOP, DESKTOP_INSETS],
    ] as const) {
      const { center, minZoom } = regionCamera(viewport, insets)
      // Píxel de cada borde de la región, medido desde el borde izquierdo.
      const px = (lon: number) =>
        viewport.width / 2 + (lon - center.longitude) * pxPerLon(minZoom)
      expect(px(REGION_BBOX[0])).toBeGreaterThan(insets.left)
      expect(px(REGION_BBOX[2])).toBeLessThan(viewport.width - insets.right)
    }
  })

  it('deja más margen al oeste: ahí van los nombres de las ciudades de la costa', () => {
    const { center, minZoom } = regionCamera(PHONE, PHONE_INSETS)
    const px = (lon: number) => PHONE.width / 2 + (lon - center.longitude) * pxPerLon(minZoom)
    const west = px(REGION_BBOX[0])
    const east = PHONE.width - px(REGION_BBOX[2])
    expect(west).toBeGreaterThan(east * 2)
    expect(west).toBeGreaterThan(50)
  })

  it('una ventana diminuta no deja un zoom mínimo absurdo', () => {
    const { minZoom } = regionCamera({ width: 300, height: 300 }, { top: 0, right: 0, bottom: 0, left: 290 })
    expect(minZoom).toBeGreaterThan(5)
  })
})
