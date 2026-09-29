/**
 * Parseo de `/events/water-cuts/geojson`: tolerante, y sin inventar datos.
 *
 * El cuerpo de ejemplo es lo que devuelve el backend con las capturas reales
 * del 23-09 (`backend/tests/test_water_cuts_endpoint.py`).
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { parseWaterCuts, resetWaterCutWarnings } from './waterCuts'

const VINA = {
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [-71.5412, -33.0213] },
  properties: {
    public_id: '5d0c1a2b-0000-4000-8000-000000000001',
    sisda: '2916567',
    comuna: 'Viña del Mar',
    tipo: 'emergencia',
    programado: false,
    motivo: 'Vida util vencida',
    calles: 'LOS PENSAMIENTOS',
    sector: null,
    inicio: '2026-09-23T14:00:00+00:00',
    fin: '2026-09-23T20:00:00+00:00',
    suministro_alternativo: false,
    url_mapa: 'https://tupuntodeagua.esval.cl/?sisda=2916567',
    visto_en: '2026-09-23T18:30:00+00:00',
  },
}

function body(features: unknown[], fuente: Record<string, unknown> = {}) {
  return {
    type: 'FeatureCollection',
    features,
    generado_en: '2026-09-23T18:31:00+00:00',
    total: features.length,
    fuente: {
      collector: 'esval_cortes_agua',
      estado: 'ok',
      ultima_corrida: '2026-09-23T18:30:05+00:00',
      ultima_lectura: '2026-09-23T18:30:00+00:00',
      detalle: null,
      ...fuente,
    },
  }
}

afterEach(() => {
  resetWaterCutWarnings()
  vi.restoreAllMocks()
})

describe('parseWaterCuts', () => {
  it('aplana la feature y conserva cada campo que llegó', () => {
    const { cuts, fuente } = parseWaterCuts(body([VINA]))
    expect(cuts).toEqual([
      {
        id: '5d0c1a2b-0000-4000-8000-000000000001',
        sisda: '2916567',
        comuna: 'Viña del Mar',
        tipo: 'emergencia',
        programado: false,
        motivo: 'Vida util vencida',
        calles: 'LOS PENSAMIENTOS',
        sector: null,
        inicio: '2026-09-23T14:00:00+00:00',
        fin: '2026-09-23T20:00:00+00:00',
        suministro_alternativo: false,
        url_mapa: 'https://tupuntodeagua.esval.cl/?sisda=2916567',
        visto_en: '2026-09-23T18:30:00+00:00',
        coordinates: [-71.5412, -33.0213],
      },
    ])
    expect(fuente.estado).toBe('ok')
    expect(fuente.ultima_lectura).toBe('2026-09-23T18:30:00+00:00')
  })

  it('un corte sin punto se conserva, con coordenadas nulas', () => {
    const { cuts } = parseWaterCuts(body([{ ...VINA, geometry: null }]))
    expect(cuts).toHaveLength(1)
    expect(cuts[0]!.coordinates).toBeNull()
  })

  it('descarta lo que no se puede seleccionar y avisa UNA vez', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const sinId = { ...VINA, properties: { ...VINA.properties, public_id: '' } }
    const { cuts } = parseWaterCuts(body([VINA, sinId, 'basura', null]))
    parseWaterCuts(body([sinId]))

    expect(cuts).toHaveLength(1)
    expect(warn).toHaveBeenCalledTimes(1)
    expect(String(warn.mock.calls[0]?.[0])).toContain('3 corte(s)')
  })

  it('no publica enlaces que no sean https ni fechas ilegibles', () => {
    const raro = {
      ...VINA,
      properties: {
        ...VINA.properties,
        url_mapa: 'http://tupuntodeagua.esval.cl/?sisda=1',
        fin: 'mañana a las 5',
        programado: 'no',
      },
    }
    const [cut] = parseWaterCuts(body([raro])).cuts
    expect(cut!.url_mapa).toBeNull()
    expect(cut!.fin).toBeNull()
    expect(cut!.programado).toBeNull()
  })

  it('una geometría absurda no llega al mapa', () => {
    const lejos = { ...VINA, geometry: { type: 'Point', coordinates: [-71.5, -133] } }
    expect(parseWaterCuts(body([lejos])).cuts[0]!.coordinates).toBeNull()
  })

  it('sin lectura de Esval, `ultima_lectura` queda nula', () => {
    const { cuts, fuente } = parseWaterCuts(
      body([], { estado: 'failing', ultima_lectura: null, detalle: 'falta ESVAL_PROXY_URL' }),
    )
    expect(cuts).toEqual([])
    expect(fuente.ultima_lectura).toBeNull()
    expect(fuente.estado).toBe('failing')
    expect(fuente.detalle).toBe('falta ESVAL_PROXY_URL')
  })

  it('un estado desconocido no se afirma', () => {
    expect(parseWaterCuts(body([], { estado: 'raro' })).fuente.estado).toBeUndefined()
  })

  it('un cuerpo que no es un objeto no revienta', () => {
    const { cuts, fuente } = parseWaterCuts('<html>')
    expect(cuts).toEqual([])
    expect(fuente.ultima_lectura).toBeNull()
  })
})
