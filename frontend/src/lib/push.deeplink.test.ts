import { describe, expect, it } from 'vitest'
import { parseDeepLink } from './push'

describe('enlace de un aviso de corte de agua', () => {
  it('lleva al corte con su punto', () => {
    const id = '8b0c6e8e-0000-0000-0000-000000000001'
    expect(parseDeepLink(`/?corte_agua=${id}&lat=-33.0245&lon=-71.5518`)).toEqual({
      kind: 'water',
      id,
      lat: -33.0245,
      lon: -71.5518,
    })
  })

  it('sin punto o con un id raro no lleva a ninguna parte', () => {
    expect(parseDeepLink('/?corte_agua=8b0c6e8e-0000-0000-0000-000000000001')).toBeNull()
    expect(parseDeepLink('/?corte_agua=<script>&lat=1&lon=2')).toBeNull()
  })
})
