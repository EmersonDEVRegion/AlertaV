import { describe, expect, it } from 'vitest'
import { STACK } from './stacking'

const level = (cls: string) => Number(cls.replace('z-', ''))

describe('STACK', () => {
  it('pone la barra superior sobre la hoja inferior', () => {
    // §I: con las dos en z-20, la hoja tapaba el panel de avisos en el iPhone.
    expect(level(STACK.header)).toBeGreaterThan(level(STACK.sheet))
  })

  it('deja la barra bajo el radar (z-40) y el modal de reporte (z-50)', () => {
    expect(level(STACK.header)).toBeLessThan(40)
  })
})
