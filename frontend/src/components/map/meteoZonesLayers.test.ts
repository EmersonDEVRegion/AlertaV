import { describe, expect, it } from 'vitest'
import {
  METEO_ZONE_FOCUS_LAYER_ID,
  METEO_ZONE_LAYER_IDS,
  focusFilter,
  meteoZoneSpecs,
} from './meteoZonesLayers'

describe('meteoZoneSpecs', () => {
  it('apagada alterna visibility, no desmonta', () => {
    const specs = meteoZoneSpecs('light', false, null)
    for (const spec of [specs.fill, specs.line, specs.strong, specs.focus]) {
      expect(spec.layout?.visibility).toBe('none')
    }
    expect(meteoZoneSpecs('dark', true, null).fill.layout?.visibility).toBe('visible')
  })

  it('el aviso es punteado y la alerta o alarma, continua', () => {
    const { line, strong } = meteoZoneSpecs('light', true, null)
    expect(line.paint?.['line-dasharray']).toBeDefined()
    expect(strong.paint?.['line-dasharray']).toBeUndefined()
    expect(line.filter).toEqual(['==', ['get', 'severidad'], 1])
    expect(strong.filter).toEqual(['>=', ['get', 'severidad'], 2])
  })

  it('el foco sólo calza con el id completo', () => {
    expect(focusFilter('5852')).toEqual(['in', ',5852,', ['concat', ',', ['get', 'ids'], ',']])
    expect(focusFilter(null)).toEqual(['==', 1, 0])
    expect(meteoZoneSpecs('light', true, '5852').focus.filter).toEqual(focusFilter('5852'))
  })

  it('el foco va arriba de todo', () => {
    expect(METEO_ZONE_LAYER_IDS.at(-1)).toBe(METEO_ZONE_FOCUS_LAYER_ID)
  })
})
