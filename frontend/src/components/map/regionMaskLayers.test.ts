// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { validateStyleMin } from '@maplibre/maplibre-gl-style-spec'
import type { LayerSpecification, StyleSpecification } from 'maplibre-gl'
import { REGION, regionMask } from '@/domain/region'
import {
  MASK_OPACITY,
  REGION_EDGE_SOURCE_ID,
  REGION_MASK_SOURCE_ID,
  regionMaskLayers,
} from './regionMaskLayers'

describe('máscara de la región', () => {
  it('es un estilo válido para MapLibre en los dos temas', () => {
    for (const theme of ['light', 'dark'] as const) {
      const { fill, line } = regionMaskLayers(theme)
      const style: StyleSpecification = {
        version: 8,
        sources: {
          [REGION_MASK_SOURCE_ID]: { type: 'geojson', data: regionMask() },
          [REGION_EDGE_SOURCE_ID]: { type: 'geojson', data: REGION },
        },
        layers: [fill as LayerSpecification, line as LayerSpecification],
      }
      expect(validateStyleMin(style)).toEqual([])
    }
  })

  it('casi opaca: tapa los rótulos de afuera', () => {
    expect(MASK_OPACITY.light).toBeGreaterThanOrEqual(0.85)
    expect(MASK_OPACITY.dark).toBeGreaterThanOrEqual(0.85)
  })

  it('va sin ancla: se monta encima de los campos y debajo de los marcadores', () => {
    const { fill, line } = regionMaskLayers('light')
    expect(fill).not.toHaveProperty('beforeId')
    expect(line).not.toHaveProperty('beforeId')
  })
})
