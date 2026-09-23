// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeVehicle } from '@/test/fixtures'
import { parseVehicleFeed, resetVehicleFeedWarnings } from './vehicleFeed'

function payload(items: unknown[], estado: unknown = 'ok') {
  return {
    generado_en: '2026-09-23T18:00:00Z',
    horas: 48,
    total: items.length,
    items,
    fuente: {
      collector: 'gbv_vehiculos',
      estado,
      ultima_corrida: '2026-09-23T17:45:00Z',
      detalle: null,
    },
  }
}

afterEach(() => {
  resetVehicleFeedWarnings()
  vi.restoreAllMocks()
})

describe('parseVehicleFeed', () => {
  it('conserva un aviso bien formado', () => {
    const item = makeVehicle()
    const parsed = parseVehicleFeed(payload([item]))
    expect(parsed.items).toEqual([item])
    expect(parsed.fuente.estado).toBe('ok')
  })

  it('descarta los avisos sin lo mínimo y avisa una sola vez por consola', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const good = makeVehicle({ id: 'bueno' })
    const parsed = parseVehicleFeed(
      payload([
        good,
        { ...makeVehicle({ id: 'sin-estado' }), estado: 'desconocido' },
        { ...makeVehicle({ id: 'sin-fecha' }), detectado_en: 'no-es-fecha' },
        { ...makeVehicle({ id: 'otra-region' }), region: 'otra' },
        'basura',
      ]),
    )
    expect(parsed.items.map((i) => i.id)).toEqual(['bueno'])
    parseVehicleFeed(payload([{ estado: 'x' }]))
    expect(warn).toHaveBeenCalledTimes(1)
  })

  it('normaliza la patente a mayúsculas y un campo vacío a null', () => {
    const [item] = parseVehicleFeed(
      payload([{ ...makeVehicle(), patente: 'lkxv55', color: '  ', anio: '2019' }]),
    ).items
    expect(item?.patente).toBe('LKXV55')
    expect(item?.color).toBeNull()
    expect(item?.anio).toBeNull()
  })

  it('un estado de fuente desconocido es «no sé», no «ciego»', () => {
    expect(parseVehicleFeed(payload([], 'raro')).fuente.estado).toBeUndefined()
    expect(parseVehicleFeed(payload([], 'failing')).fuente.estado).toBe('failing')
  })

  it('una respuesta sin forma no revienta: lista vacía', () => {
    expect(parseVehicleFeed(null).items).toEqual([])
    expect(parseVehicleFeed({ items: 'x' }).items).toEqual([])
  })
})
