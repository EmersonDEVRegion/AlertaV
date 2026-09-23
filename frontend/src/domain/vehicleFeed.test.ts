// @vitest-environment node
/**
 * Reglas del radar de vehículos.
 *
 * Lo que fija este archivo es la regla de las 48 h del lado del cliente, que es
 * defensiva: el backend ya filtra, pero una respuesta cacheada o una pestaña
 * abierta toda la tarde no pueden mostrar un aviso que ya salió de la ventana.
 */

import { describe, expect, it } from 'vitest'
import { VEHICLE_NOW, makeVehicle } from '@/test/fixtures'
import {
  eventDateLabel,
  formatCalendarDay,
  formatPatente,
  isRecent,
  vehicleTitle,
  visibleItems,
  withinWindow,
} from './vehicleFeed'

const HOUR = 3_600_000

describe('ventana de 48 h', () => {
  it('incluye lo visto hace 47 h y excluye lo visto hace 49 h', () => {
    expect(withinWindow(makeVehicle({}, 47), VEHICLE_NOW)).toBe(true)
    expect(withinWindow(makeVehicle({}, 48), VEHICLE_NOW)).toBe(true)
    expect(withinWindow(makeVehicle({}, 49), VEHICLE_NOW)).toBe(false)
  })

  it('descarta una fecha que no es fecha', () => {
    expect(withinWindow(makeVehicle({ detectado_en: 'ayer' }), VEHICLE_NOW)).toBe(false)
  })

  it('tolera unos minutos de reloj atrasado, pero no una hora en el futuro', () => {
    expect(withinWindow(makeVehicle({}, -0.05), VEHICLE_NOW)).toBe(true)
    expect(withinWindow(makeVehicle({}, -1), VEHICLE_NOW)).toBe(false)
  })

  it('un aviso sale de la lista cuando el reloj avanza, sin refetch', () => {
    const items = [makeVehicle({ id: 'a' }, 47.5), makeVehicle({ id: 'b' }, 2)]
    expect(visibleItems(items, VEHICLE_NOW).map((i) => i.id)).toEqual(['b', 'a'])
    expect(visibleItems(items, VEHICLE_NOW + HOUR).map((i) => i.id)).toEqual(['b'])
  })

  it('ordena del más nuevo al más viejo', () => {
    const items = [
      makeVehicle({ id: 'viejo' }, 30),
      makeVehicle({ id: 'nuevo' }, 1),
      makeVehicle({ id: 'medio' }, 10),
    ]
    expect(visibleItems(items, VEHICLE_NOW).map((i) => i.id)).toEqual(['nuevo', 'medio', 'viejo'])
  })
})

describe('reciente (< 6 h)', () => {
  it('marca lo de menos de 6 h y no lo de 6 h o más', () => {
    expect(isRecent(makeVehicle({}, 5.9), VEHICLE_NOW)).toBe(true)
    expect(isRecent(makeVehicle({}, 6), VEHICLE_NOW)).toBe(false)
  })
})

describe('patente', () => {
  it.each([
    ['LKXV55', 'LK·XV·55'],
    ['YW8869', 'YW·88·69'],
    ['RGT012', 'RGT·012'],
    ['lkxv55', 'LK·XV·55'],
    // Formato desconocido: se devuelve tal cual, sin inventar agrupamiento.
    ['ABC1', 'ABC1'],
  ])('%s → %s', (raw, expected) => {
    expect(formatPatente(raw)).toBe(expected)
  })
})

describe('fecha del delito', () => {
  it('es un día de calendario: no se corre al día anterior por la zona horaria', () => {
    // `new Date('2026-09-22')` es medianoche UTC = 21 sep en Chile. La función
    // no puede pasar por ahí.
    expect(formatCalendarDay('2026-09-22')).toBe('22 sep')
    expect(formatCalendarDay('2026-01-01', true)).toBe('1 ene 2026')
  })

  it('rechaza lo que no es AAAA-MM-DD', () => {
    expect(formatCalendarDay('22/09/2026')).toBeNull()
    expect(formatCalendarDay('2026-13-01')).toBeNull()
    expect(formatCalendarDay(null)).toBeNull()
  })

  it('en un recuperado sigue siendo la fecha del robo', () => {
    expect(eventDateLabel(makeVehicle({ estado: 'recuperado' }))).toBe('robo 22 sep')
  })

  it('en un abandonado usa el tiempo que publica GBV', () => {
    const item = makeVehicle({ estado: 'abandonado', fecha_delito: null, tiempo_abandono: '4 días' })
    expect(eventDateLabel(item)).toBe('hace 4 días')
  })
})

describe('título', () => {
  it('marca y modelo; si faltan, el tipo; si falta todo, «Vehículo»', () => {
    expect(vehicleTitle(makeVehicle())).toBe('Toyota Rav4')
    expect(vehicleTitle(makeVehicle({ marca: null, modelo: null }))).toBe('Auto')
    expect(vehicleTitle(makeVehicle({ marca: null, modelo: null, tipo_vehiculo: null }))).toBe(
      'Vehículo',
    )
  })
})
