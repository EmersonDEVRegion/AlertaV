import { describe, expect, it } from 'vitest'
import { EMPTY_METEO_ZONES, boundsOfWarning, parseMeteoZones } from './meteoZones'

const square = (w: number, s: number, e: number, n: number) => ({
  type: 'Polygon',
  coordinates: [
    [
      [w, s],
      [e, s],
      [e, n],
      [w, n],
      [w, s],
    ],
  ],
})

const payload = {
  type: 'FeatureCollection',
  generado_en: '2026-10-17T03:00:00Z',
  features: [
    {
      type: 'Feature',
      geometry: square(-71.8, -33.6, -71.5, -32.3),
      properties: {
        zona: 'Litoral',
        nombre: 'Litoral',
        severidad: 2,
        tipo: 'Alerta',
        avisos: 2,
        fenomenos: 'Precipitaciones · Tormentas Eléctricas',
        ids: '5853,5852',
      },
    },
    {
      type: 'Feature',
      geometry: square(-70.6, -33.0, -70.0, -32.3),
      properties: { zona: 'Cordillera', nombre: 'Cordillera', severidad: 1, tipo: 'Aviso', avisos: 1, fenomenos: 'Tormentas Eléctricas', ids: '5852' },
    },
    { type: 'Feature', geometry: null, properties: { zona: 'Rota', severidad: 1, tipo: 'Aviso' } },
    { type: 'Feature', geometry: square(0, 0, 1, 1), properties: { zona: 'X', severidad: 7, tipo: 'Aviso' } },
  ],
}

describe('parseMeteoZones', () => {
  it('se queda con las zonas válidas', () => {
    const data = parseMeteoZones(payload)
    expect(data.features.map((f) => f.properties.zona)).toEqual(['Litoral', 'Cordillera'])
    expect(data.features[0]!.properties.tipo).toBe('Alerta')
  })

  it('una respuesta rara es una capa vacía, no un error', () => {
    expect(parseMeteoZones(null)).toBe(EMPTY_METEO_ZONES)
    expect(parseMeteoZones({ features: 'no' })).toBe(EMPTY_METEO_ZONES)
  })
})

describe('boundsOfWarning', () => {
  it('encuadra todas las zonas del aviso', () => {
    const data = parseMeteoZones(payload)
    expect(boundsOfWarning(data, '5852')).toEqual([-71.8, -33.6, -70.0, -32.3])
    expect(boundsOfWarning(data, '5853')).toEqual([-71.8, -33.6, -71.5, -32.3])
  })

  it('un id que es prefijo de otro no calza', () => {
    expect(boundsOfWarning(parseMeteoZones(payload), '585')).toBeNull()
  })
})
