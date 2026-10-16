import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { setGlassLevel, useGlassLevel } from './glassStore'

describe('niveles de cristal (§D)', () => {
  it('arranca en equilibrado y lo marca en <html>', () => {
    const { result } = renderHook(() => useGlassLevel())
    expect(result.current).toBe('equilibrado')
    expect(document.documentElement.dataset.glass).toBe('equilibrado')
  })

  it('cambiar el nivel lo aplica y lo guarda en el dispositivo', () => {
    const { result } = renderHook(() => useGlassLevel())
    act(() => setGlassLevel('solido'))
    expect(result.current).toBe('solido')
    expect(document.documentElement.dataset.glass).toBe('solido')
    expect(localStorage.getItem('alertav:cristal')).toBe('solido')
    act(() => setGlassLevel('equilibrado'))
  })
})
