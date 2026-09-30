import { describe, expect, it } from 'vitest'
import { makeIncident } from '@/test/fixtures'
import { lastBomberosDispatch } from './lastDispatch'

describe('lastBomberosDispatch', () => {
  it('toma el first_seen_at más reciente entre los incidentes de Bomberos', () => {
    const at = lastBomberosDispatch([
      makeIncident({ code: 'a', sources: ['bomberos'], first_seen_at: '2026-09-30T12:56:00Z' }),
      makeIncident({ code: 'b', sources: ['bomberos', 'citizen'], first_seen_at: '2026-09-30T13:01:00Z' }),
      makeIncident({ code: 'c', sources: ['bomberos'], first_seen_at: '2026-09-30T08:30:00Z' }),
    ])
    expect(at).toBe(Date.parse('2026-09-30T13:01:00Z'))
  })

  it('ignora las otras fuentes aunque sean más recientes', () => {
    const at = lastBomberosDispatch([
      makeIncident({ code: 'a', sources: ['bomberos'], first_seen_at: '2026-09-30T13:01:00Z' }),
      makeIncident({ code: 'b', sources: ['chilquinta'], first_seen_at: '2026-09-30T14:54:00Z' }),
    ])
    expect(at).toBe(Date.parse('2026-09-30T13:01:00Z'))
  })

  it('sin despachos devuelve null', () => {
    expect(lastBomberosDispatch([makeIncident({ sources: ['conaf'] })])).toBeNull()
    expect(lastBomberosDispatch([])).toBeNull()
  })
})
