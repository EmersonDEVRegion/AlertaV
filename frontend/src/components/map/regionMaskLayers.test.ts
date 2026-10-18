// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { createExpression, validateStyleMin } from '@maplibre/maplibre-gl-style-spec'
import type { LayerSpecification, StyleSpecification } from 'maplibre-gl'
import { REGION, REGION_VIGNETTE } from '@/domain/region'
import {
  REGION_EDGE_SOURCE_ID,
  REGION_MASK_SOURCE_ID,
  VIGNETTE_TOP,
  regionMaskLayers,
  vignetteOpacity,
} from './regionMaskLayers'

/** Evalúa la opacidad de una capa con sus propiedades. */
function alpha(top: number, f: number, fp: number): number {
  const parsed = createExpression(vignetteOpacity(top), 'layers[0].paint.fill-opacity')
  if (parsed.result !== 'success') throw new Error('expresión inválida')
  return parsed.value.evaluate({ zoom: 8 }, {
    type: 'Polygon',
    properties: { f, fp },
  } as never) as number
}

describe('viñeta de la región', () => {
  it('es un estilo válido para MapLibre en los dos temas', () => {
    for (const theme of ['light', 'dark'] as const) {
      const { fill, line } = regionMaskLayers(theme)
      const style: StyleSpecification = {
        version: 8,
        sources: {
          [REGION_MASK_SOURCE_ID]: { type: 'geojson', data: REGION_VIGNETTE },
          [REGION_EDGE_SOURCE_ID]: { type: 'geojson', data: REGION },
        },
        layers: [fill as LayerSpecification, line as LayerSpecification],
      }
      expect(validateStyleMin(style)).toEqual([])
    }
  })

  it('el apilado de las capas da el tope por la oscuridad de cada tramo', () => {
    for (const top of [VIGNETTE_TOP.light, VIGNETTE_TOP.dark]) {
      let through = 1 // lo que deja pasar el apilado
      for (const { properties } of REGION_VIGNETTE.features) {
        through *= 1 - alpha(top, properties.f, properties.fp)
        expect(1 - through).toBeCloseTo(top * properties.f, 6)
      }
      expect(1 - through).toBeCloseTo(top, 6)
    }
  })

  it('el tope apenas deja adivinar lo lejano', () => {
    for (const top of Object.values(VIGNETTE_TOP)) {
      expect(top).toBeGreaterThanOrEqual(0.85)
      expect(top).toBeLessThanOrEqual(0.95)
    }
  })

  it('sin antialias (no deja hilos entre capas) y sin ancla (encima de los campos)', () => {
    const { fill, line } = regionMaskLayers('dark')
    const paint = (fill as { paint?: Record<string, unknown> }).paint
    expect(paint?.['fill-antialias']).toBe(false)
    expect(fill).not.toHaveProperty('beforeId')
    expect(line).not.toHaveProperty('beforeId')
  })
})
