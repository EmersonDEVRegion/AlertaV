import { describe, expect, it } from 'vitest'
import { DEFAULT_LAYER_VISIBILITY, DEFAULT_PROVIDER_VISIBILITY, hiddenFilterCount } from './layers'

describe('hiddenFilterCount', () => {
  it('cero con todo a la vista', () => {
    expect(hiddenFilterCount(DEFAULT_LAYER_VISIBILITY, DEFAULT_PROVIDER_VISIBILITY, false)).toBe(0)
  })

  it('cuenta capas, empresas y verificados', () => {
    expect(
      hiddenFilterCount({ ...DEFAULT_LAYER_VISIBILITY, fire: false }, { chilquinta: true, cge: false }, true),
    ).toBe(3)
  })

  it('con la luz apagada, las empresas no suman: ya cuenta la capa', () => {
    expect(
      hiddenFilterCount({ ...DEFAULT_LAYER_VISIBILITY, power: false }, { chilquinta: false, cge: false }, false),
    ).toBe(1)
  })
})
