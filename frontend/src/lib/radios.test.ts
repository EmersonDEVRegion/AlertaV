import { describe, expect, it } from 'vitest'
import {
  PASOS,
  formatRadio,
  metrosDePosicion,
  posicionDeMetros,
  radiosEfectivos,
} from './radios'

describe('escala del deslizador', () => {
  it('0 apaga y los extremos son 300 m y 20 km', () => {
    expect(metrosDePosicion(0)).toBe(0)
    expect(metrosDePosicion(1)).toBe(300)
    expect(metrosDePosicion(PASOS)).toBe(20_000)
  })

  it('ida y vuelta conserva los radios por defecto', () => {
    for (const metros of [1000, 2000, 3000, 5000]) {
      expect(metrosDePosicion(posicionDeMetros(metros))).toBe(metros)
    }
  })

  it('es creciente', () => {
    let anterior = 0
    for (let p = 1; p <= PASOS; p += 7) {
      const metros = metrosDePosicion(p)
      expect(metros).toBeGreaterThanOrEqual(anterior)
      anterior = metros
    }
  })
})

describe('formatRadio', () => {
  it('se lee como en Chile', () => {
    expect(formatRadio(0)).toBe('No avisar')
    expect(formatRadio(650)).toBe('650 m')
    expect(formatRadio(1000)).toBe('1 km')
    expect(formatRadio(1500)).toBe('1,5 km')
    expect(formatRadio(12_000)).toBe('12 km')
  })
})

describe('radiosEfectivos', () => {
  it('lo elegido manda sobre el servidor, y el servidor sobre el respaldo', () => {
    const r = radiosEfectivos({ fire: 6000 }, { power: 0 })
    expect(r.fire).toBe(6000)
    expect(r.power).toBe(0)
    expect(r.traffic).toBe(2000)
  })
})
