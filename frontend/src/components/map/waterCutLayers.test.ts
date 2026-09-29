import { validateStyleMin } from '@maplibre/maplibre-gl-style-spec'
import type { LayerSpecification, StyleSpecification } from 'maplibre-gl'
import { describe, expect, it } from 'vitest'
import { ICON_IDS, WATER_ICON } from '@/domain/emergencyIcons'
import { toWaterCutFeatureCollection } from '@/lib/geojson'
import { makeWaterCut } from '@/test/fixtures'
import {
  WATER_HIT_LAYER_ID,
  WATER_SELECTED_LAYER_ID,
  waterGlyphLayer,
  waterHitLayer,
  waterPinLayer,
  waterSelectedLayer,
} from './waterCutLayers'

const THEMES = ['light', 'dark'] as const

function styleWith(layers: LayerSpecification[]): StyleSpecification {
  return {
    version: 8,
    sources: { water: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } } },
    layers: layers.map((layer) => ({ ...layer, source: 'water' })),
  } as StyleSpecification
}

function allLayers(theme: 'light' | 'dark', visible: boolean) {
  return [
    waterPinLayer(theme, visible),
    waterGlyphLayer(visible),
    waterSelectedLayer(theme, 'agua-1', visible),
    waterSelectedLayer(theme, null, visible),
    waterHitLayer(visible),
  ]
}

describe('capas de cortes de agua', () => {
  it('pasan el validador de MapLibre en ambos temas, encendidas y apagadas', () => {
    for (const theme of THEMES) {
      for (const visible of [true, false]) {
        const layers = allLayers(theme, visible).map((layer, i) => ({ ...layer, id: `${layer.id}-${i}` }))
        const errors = validateStyleMin(styleWith(layers as unknown as LayerSpecification[]))
        expect({ theme, visible, errors: errors.map((e) => e.message) }).toEqual({
          theme,
          visible,
          errors: [],
        })
      }
    }
  })

  it('apagar la fila es `visibility`, en todas las capas, incluido el objetivo táctil', () => {
    for (const layer of allLayers('light', false)) {
      expect(layer.layout?.visibility).toBe('none')
    }
    for (const layer of allLayers('light', true)) {
      expect(layer.layout?.visibility).toBe('visible')
    }
  })

  it('la gota es un icono registrado', () => {
    expect(ICON_IDS).toContain(WATER_ICON)
    expect(waterGlyphLayer(true).layout?.['icon-image']).toBe(WATER_ICON)
  })

  it('el anillo sigue al corte seleccionado y a ninguno sin selección', () => {
    expect(JSON.stringify(waterSelectedLayer('dark', 'agua-1', true).filter)).toContain('"agua-1"')
    expect(JSON.stringify(waterSelectedLayer('dark', null, true).filter)).toBe(
      '["==",["get","water_id"]," "]',
    )
    expect(waterSelectedLayer('dark', null, true).id).toBe(WATER_SELECTED_LAYER_ID)
    expect(waterHitLayer(true).id).toBe(WATER_HIT_LAYER_ID)
  })
})

describe('toWaterCutFeatureCollection', () => {
  it('sólo dibuja los que tienen punto, con un id propio y la urgencia para ordenar', () => {
    const data = toWaterCutFeatureCollection([
      makeWaterCut({ id: 'e', programado: false }),
      makeWaterCut({ id: 'p', programado: true }),
      makeWaterCut({ id: 'x', programado: null }),
      makeWaterCut({ id: 'sin-punto', coordinates: null }),
    ])
    expect(data.features.map((f) => f.properties)).toEqual([
      { water_id: 'e', emergencia: 1 },
      { water_id: 'p', emergencia: 0 },
      { water_id: 'x', emergencia: 0 },
    ])
    // Sin `code`: un corte de agua no puede confundirse con un incidente al tocarlo.
    expect(JSON.stringify(data)).not.toContain('"code"')
  })
})
