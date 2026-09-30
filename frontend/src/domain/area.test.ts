import { describe, expect, it } from 'vitest'
import { makeIncident } from '@/test/fixtures'
import { areaLabel, inArea } from './area'

describe('área del historial', () => {
  it('una comuna se compara sin tildes ni mayúsculas', () => {
    const fire = makeIncident({ commune: 'QUILPUE' })
    expect(inArea(fire, { kind: 'commune', name: 'Quilpué' })).toBe(true)
    expect(inArea(fire, { kind: 'commune', name: 'Villa Alemana' })).toBe(false)
    expect(inArea(makeIncident({ commune: null }), { kind: 'commune', name: 'Quilpué' })).toBe(
      false,
    )
  })

  it('un lugar guardado, por distancia: 5 km, como los avisos', () => {
    const casa = { kind: 'place' as const, id: 'a', name: 'Casa', lat: -33.047, lon: -71.442 }
    // ~1,1 km al sur
    expect(inArea(makeIncident({ lat: -33.057, lon: -71.442 }), casa)).toBe(true)
    // ~11 km al oeste
    expect(inArea(makeIncident({ lat: -33.047, lon: -71.56 }), casa)).toBe(false)
  })

  it('el chip dice qué se está mirando', () => {
    expect(areaLabel({ kind: 'commune', name: 'VIÑA DEL MAR' })).toBe('Viña del Mar')
    expect(areaLabel({ kind: 'place', id: 'a', name: 'Casa', lat: 0, lon: 0 })).toBe(
      'Cerca de Casa · 5 km',
    )
  })
})
