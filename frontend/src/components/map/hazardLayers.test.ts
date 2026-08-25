// @vitest-environment node
/**
 * Tests de estilo y jerarquía de la capa de amenaza.
 *
 * El más importante es el del `beforeId`: si esa constante deja de coincidir
 * con una capa real, MapLibre ignora el anclaje EN SILENCIO y la amenaza pasa a
 * dibujarse sobre los incendios. No hay error, no hay aviso: sólo un mapa en el
 * que las emergencias quedan tapadas por un modelo probabilístico.
 */

import { describe, expect, it } from 'vitest'
import {
  HAZARD_BEFORE_ID,
  HAZARD_FILL_LAYER_ID,
  hazardFillLayer,
  hazardLineLayer,
} from './hazardLayers'
import { reachFillLayer } from './overlayLayers'
import { HAZARD_RAMP, HAZARD_VARIABLE } from '@/domain/hazardSymbology'

describe('jerarquía de dibujo', () => {
  it('se ancla a una capa de emergencia que existe de verdad', () => {
    expect(HAZARD_BEFORE_ID).toBe(reachFillLayer.id)
  })

  it('no se ancla a sí misma', () => {
    expect(HAZARD_BEFORE_ID).not.toBe(HAZARD_FILL_LAYER_ID)
  })
})

describe('visibilidad', () => {
  it('se apaga por `visibility`, nunca desmontando', () => {
    expect(hazardFillLayer('dark', false).layout?.visibility).toBe('none')
    expect(hazardFillLayer('dark', true).layout?.visibility).toBe('visible')
    expect(hazardLineLayer('dark', false).layout?.visibility).toBe('none')
  })
})

describe('estilo sobre mapa oscuro', () => {
  it('mantiene el relleno translúcido en ambos temas', () => {
    for (const theme of ['light', 'dark'] as const) {
      const opacity = hazardFillLayer(theme, true).paint?.['fill-opacity'] as number
      expect(opacity).toBeGreaterThan(0)
      // Por sobre ~0,5 la capa deja de ser contexto y empieza a tapar el mapa.
      expect(opacity).toBeLessThanOrEqual(0.4)
    }
  })

  it('usa una rampa distinta por tema, no la misma con otra opacidad', () => {
    const light = HAZARD_RAMP.light.stops.map(([, c]) => c)
    const dark = HAZARD_RAMP.dark.stops.map(([, c]) => c)
    expect(light).not.toEqual(dark)
  })

  it('invierte la dirección de luminosidad según el tema', () => {
    // Claro: más amenaza = más oscuro. Oscuro: más amenaza = más brillante.
    const luminance = (hex: string) => {
      const v = hex.replace('#', '')
      return [0, 2, 4].reduce((sum, i) => sum + parseInt(v.slice(i, i + 2), 16), 0)
    }
    const lightStops = HAZARD_RAMP.light.stops.map(([, c]) => luminance(c))
    const darkStops = HAZARD_RAMP.dark.stops.map(([, c]) => luminance(c))

    expect(lightStops[0]!).toBeGreaterThan(lightStops[lightStops.length - 1]!)
    expect(darkStops[0]!).toBeLessThan(darkStops[darkStops.length - 1]!)
  })

  it('no invade la paleta cálida de las emergencias', () => {
    const emergencyHexes = ['#dc2626', '#eab308', '#ea580c', '#b91c1c', '#f97316', '#991b1b']
    const hazardHexes = [...HAZARD_RAMP.light.stops, ...HAZARD_RAMP.dark.stops].map(
      ([, c]) => c.toLowerCase(),
    )
    for (const hex of emergencyHexes) expect(hazardHexes).not.toContain(hex)
  })

  it('desactiva el antialias del relleno para no dibujar costuras', () => {
    expect(hazardFillLayer('dark', true).paint?.['fill-antialias']).toBe(false)
  })

  it('colorea por la variable documentada del artefacto', () => {
    const expr = JSON.stringify(hazardFillLayer('dark', true).paint?.['fill-color'])
    expect(expr).toContain(HAZARD_VARIABLE)
    expect(expr).toContain('interpolate')
  })
})
