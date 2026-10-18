import { validateStyleMin } from '@maplibre/maplibre-gl-style-spec'
import type { LayerSpecification, StyleSpecification } from 'maplibre-gl'
import { describe, expect, it } from 'vitest'
import { toServiceAreaFeatureCollection, waterAreaId } from '@/lib/geojson'
import { makeIncident, makeWaterCut } from '@/test/fixtures'
import {
  SERVICE_AREA_SOURCE_ID,
  serviceAreaFillLayer,
  serviceAreaLineLayer,
  serviceAreaSelectedFillLayer,
  serviceAreaSelectedLineLayer,
} from './serviceAreaLayers'

const ANILLO = [
  [-71.58, -33.06],
  [-71.57, -33.06],
  [-71.57, -33.05],
  [-71.58, -33.06],
]
const AREA = { type: 'MultiPolygon' as const, coordinates: [[ANILLO]] }

function outage(code: string, over: Record<string, unknown> = {}) {
  return makeIncident({
    code,
    type: 'power_outage',
    sources: ['chilquinta'],
    outage: {
      provider: 'chilquinta',
      affected_clients: 10,
      estimated_restoration: null,
      sector: null,
      outage_count: 1,
      area: AREA,
      ...over,
    },
  })
}

describe('capas de áreas de cortes', () => {
  it('pasan el validador de MapLibre en ambos temas, con y sin selección', () => {
    for (const theme of ['light', 'dark'] as const) {
      const layers = [
        serviceAreaFillLayer(),
        serviceAreaLineLayer(theme),
        serviceAreaSelectedFillLayer([]),
        serviceAreaSelectedLineLayer(theme, ['INC-1', 'water:x']),
      ].map((layer) => ({ ...layer, source: SERVICE_AREA_SOURCE_ID }))
      const style = {
        version: 8,
        sources: {
          [SERVICE_AREA_SOURCE_ID]: {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] },
          },
        },
        layers: layers as unknown as LayerSpecification[],
      } as StyleSpecification
      expect(validateStyleMin(style).map((e) => e.message)).toEqual([])
    }
  })

  it('las áreas tenues sólo aparecen desde el zoom de barrio', () => {
    expect(serviceAreaFillLayer().minzoom).toBeGreaterThan(11)
    expect(serviceAreaSelectedFillLayer(['INC-1']).minzoom).toBeUndefined()
  })
})

describe('toServiceAreaFeatureCollection', () => {
  it('junta luz y agua, cada una con el id que usa su selección', () => {
    const coleccion = toServiceAreaFeatureCollection(
      [outage('INC-1'), outage('INC-2', { area: null })],
      [makeWaterCut({ area: AREA }), makeWaterCut({ id: 'sin-area' })],
    )
    expect(coleccion.features.map((f) => f.properties)).toEqual([
      { area_id: 'INC-1', kind: 'chilquinta', is_closed: false },
      { area_id: waterAreaId(makeWaterCut().id), kind: 'water', is_closed: false },
    ])
  })

  it('un área rota se omite: el corte queda como pin', () => {
    const rota = outage('INC-3', { area: { type: 'MultiPolygon', coordinates: [[[[0, 0]]]] } })
    expect(toServiceAreaFeatureCollection([rota], []).features).toEqual([])
  })
})
