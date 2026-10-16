import { describe, expect, it } from 'vitest'
import type { Incident } from '@/api/types'
import { INCERTIDUMBRE_M, incertidumbreDe } from '@/domain/locationPrecision'
import { UBICACION_PRECISION_LABEL } from '@/domain/labels'
import { toFeatureCollection } from '@/lib/geojson'
import { uncertaintyLayer } from './incidentLayers'

describe('círculo de incertidumbre', () => {
  it('el radio sale de la precisión y lo exacto no lleva círculo', () => {
    expect(incertidumbreDe('intersection')).toBe(60)
    expect(incertidumbreDe('street')).toBe(300)
    expect(incertidumbreDe('sector')).toBe(600)
    expect(incertidumbreDe('exacta')).toBe(0)
    expect(incertidumbreDe('address')).toBe(0)
    expect(incertidumbreDe('despacho')).toBe(0)
    expect(incertidumbreDe(null)).toBe(0)
    expect(incertidumbreDe(undefined)).toBe(0)
  })

  it('el rótulo de la ficha dice el mismo radio que dibuja el mapa', () => {
    for (const [precision, metros] of Object.entries(INCERTIDUMBRE_M)) {
      const rotulo = UBICACION_PRECISION_LABEL[precision as keyof typeof INCERTIDUMBRE_M]
      if (metros > 0) expect(rotulo).toContain(`± ${metros} m`)
    }
  })

  it('viaja en el GeoJSON del mapa', () => {
    const incidente = {
      code: 'INC-2026-00001',
      type: 'structural_fire',
      lat: -33.02,
      lon: -71.55,
      status: 'active',
      confidence: 1,
      is_official_confirmed: true,
      ubicacion_precision: 'street',
    } as unknown as Incident
    const feature = toFeatureCollection([incidente]).features[0]
    expect(feature?.properties.incertidumbre_m).toBe(300)
  })

  it('convierte metros a píxeles en base 2: 300 m ≈ 300 px en zoom 16 a 33° S', () => {
    const radio = uncertaintyLayer.paint?.['circle-radius'] as unknown as unknown[]
    expect(radio.slice(0, 3)).toEqual(['interpolate', ['exponential', 2], ['zoom']])
    const [, , , z13, en13, z22, en22] = radio as [unknown, unknown, unknown, number, unknown[], number, unknown[]]
    const factor = (zoom: number, tope: unknown[]) => {
      expect(tope.slice(0, 2)).toEqual(['*', ['get', 'incertidumbre_m']])
      return (tope[2] as number) / 2 ** (zoom - 16)
    }
    expect(factor(z13, en13) * 300).toBeCloseTo(299.5, 0)
    expect(factor(z22, en22) * 300).toBeCloseTo(299.5, 0)
  })
})
