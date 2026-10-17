import { describe, expect, it } from 'vitest'
import { LEVEL_COLOR_EXPRESSION } from '@/domain/palette'
import { casingLayer, closedRingLayer, selectedLayer, unverifiedLayer } from './incidentLayers'
import { PIN_EDGE, PIN_INK } from './pinTheme'
import { seismicSelectedLayer } from './seismicLayers'

const THEMES = ['light', 'dark'] as const

describe('marcadores de incidentes según el tema', () => {
  it('la placa bajo el icono toma el fondo del tema: nunca blanca en oscuro', () => {
    expect(casingLayer('light').paint?.['circle-color']).toBe('#ffffff')
    expect(casingLayer('dark').paint?.['circle-color']).toBe(PIN_EDGE.dark)
    expect(casingLayer('dark').paint?.['circle-color']).not.toBe('#ffffff')
  })

  it('la placa lleva un aro fino del color de severidad en los dos temas', () => {
    for (const theme of THEMES) {
      const paint = casingLayer(theme).paint
      expect(paint?.['circle-stroke-color']).toEqual(LEVEL_COLOR_EXPRESSION)
      expect(paint?.['circle-stroke-width']).toBeGreaterThan(0)
      expect(paint?.['circle-stroke-width']).toBeLessThanOrEqual(2)
    }
  })

  it('el centro hueco es del color de la placa, no un punto blanco', () => {
    for (const theme of THEMES) {
      expect(unverifiedLayer(theme).paint?.['circle-color']).toBe(PIN_EDGE[theme])
    }
  })

  it('el anillo de selección contrasta con el tema (incidentes y sismos)', () => {
    for (const theme of THEMES) {
      expect(selectedLayer('INC-1', theme).paint?.['circle-stroke-color']).toBe(PIN_INK[theme])
      expect(seismicSelectedLayer('us1', theme).paint?.['circle-stroke-color']).toBe(PIN_INK[theme])
    }
    expect(PIN_INK.dark).not.toBe(PIN_EDGE.dark)
  })

  it('el anillo de cerrado cambia de tono con el tema', () => {
    expect(closedRingLayer('light').paint?.['circle-stroke-color']).not.toBe(
      closedRingLayer('dark').paint?.['circle-stroke-color'],
    )
  })
})
