// @vitest-environment node
import { describe, expect, it } from 'vitest'
import {
  NEARBY_ZOOM,
  POSITION_DEADLINE_MS,
  homePlace,
  shouldGoToPosition,
  startView,
  type StartView,
} from './initialView'
import type { RegionCamera } from './regionCamera'

const CAMERA: RegionCamera = {
  minZoom: 7.6,
  maxBounds: [-73, -34.8, -69.2, -31.2],
  center: { longitude: -70.9, latitude: -33.0 },
}
const casa = { id: '1', name: 'Casa', lat: -33.02, lon: -71.55 }
const trabajo = { id: '2', name: 'Trabajo', lat: -33.04, lon: -71.62 }
const enSantiago = { id: '3', name: 'Depto', lat: -33.45, lon: -70.65 }

const REGION_VIEW: StartView = { kind: 'region', ...CAMERA.center, zoom: CAMERA.minZoom }
const CASA_VIEW: StartView = { kind: 'place', longitude: casa.lon, latitude: casa.lat, zoom: NEARBY_ZOOM }
const VINA = { lat: -33.0245, lon: -71.5518 }
const QUILLOTA = { lat: -32.88, lon: -71.25 }

describe('homePlace', () => {
  it('prefiere «Casa», sin importar el orden ni las mayúsculas', () => {
    expect(homePlace([trabajo, { ...casa, name: ' casa ' }])?.id).toBe('1')
  })
  it('sin «Casa», el primero; sin lugares, ninguno', () => {
    expect(homePlace([trabajo])?.id).toBe('2')
    expect(homePlace([])).toBeNull()
  })
})

describe('startView', () => {
  it('sin lugares: la región entera al zoom mínimo', () => {
    expect(startView([], CAMERA)).toEqual(REGION_VIEW)
  })
  it('con Casa: Casa al zoom de «Mis lugares»', () => {
    expect(startView([trabajo, casa], CAMERA)).toEqual(CASA_VIEW)
  })
  it('un lugar fuera de la región no se usa', () => {
    expect(startView([enSantiago], CAMERA)).toEqual(REGION_VIEW)
  })
})

describe('shouldGoToPosition', () => {
  const base = { untouched: true, elapsedMs: 1500, start: REGION_VIEW }

  it('desde la región, va a la ubicación', () => {
    expect(shouldGoToPosition({ ...base, position: QUILLOTA })).toBe(true)
  })
  it('nunca si alguien movió el mapa', () => {
    expect(shouldGoToPosition({ ...base, untouched: false, position: QUILLOTA })).toBe(false)
  })
  it('nunca si la lectura llegó tarde', () => {
    expect(
      shouldGoToPosition({ ...base, elapsedMs: POSITION_DEADLINE_MS + 1, position: QUILLOTA }),
    ).toBe(false)
  })
  it('nunca fuera de la región', () => {
    expect(shouldGoToPosition({ ...base, position: { lat: -33.45, lon: -70.65 } })).toBe(false)
  })
  it('desde Casa, sólo si está a más de 1 km', () => {
    expect(shouldGoToPosition({ ...base, start: CASA_VIEW, position: VINA })).toBe(false)
    expect(shouldGoToPosition({ ...base, start: CASA_VIEW, position: QUILLOTA })).toBe(true)
  })
})
