import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useNow } from './useNow'

describe('useNow — un reloj compartido por cadencia', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    vi.setSystemTime(new Date('2026-09-29T12:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('avanza con la cadencia pedida', () => {
    const { result } = renderHook(() => useNow(30_000))
    const start = result.current

    act(() => {
      vi.advanceTimersByTime(30_000)
    })
    expect(result.current - start).toBe(30_000)
  })

  it('cien lectores de la misma cadencia comparten un solo temporizador', () => {
    const spy = vi.spyOn(globalThis, 'setInterval')
    const hooks = Array.from({ length: 100 }, () => renderHook(() => useNow(7_000)))

    expect(spy).toHaveBeenCalledTimes(1)
    act(() => {
      vi.advanceTimersByTime(7_000)
    })
    const values = new Set(hooks.map((h) => h.result.current))
    expect(values.size).toBe(1)

    for (const hook of hooks) hook.unmount()
    spy.mockRestore()
  })

  it('se apaga con el último lector y al volver arranca con la hora actual', () => {
    const clear = vi.spyOn(globalThis, 'clearInterval')
    const first = renderHook(() => useNow(11_000))
    first.unmount()
    expect(clear).toHaveBeenCalledTimes(1)

    // Una hora sin nadie mirando: el reloj no puede volver con la hora vieja.
    vi.setSystemTime(new Date('2026-09-29T13:00:00Z'))
    const again = renderHook(() => useNow(11_000))
    expect(again.result.current).toBe(new Date('2026-09-29T13:00:00Z').getTime())
    again.unmount()
    clear.mockRestore()
  })
})
