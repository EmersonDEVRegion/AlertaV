import { afterEach, describe, expect, it, vi } from 'vitest'
import { getLastPosition, reportPosition, resetPosition, subscribePosition } from './positionStore'

afterEach(() => resetPosition())

describe('positionStore', () => {
  it('guarda la lectura y avisa a quien escucha', () => {
    const listener = vi.fn()
    subscribePosition(listener)
    reportPosition({ lat: -33.04, lon: -71.6, accuracyM: 20, at: 1000 })
    expect(getLastPosition()).toEqual({ lat: -33.04, lon: -71.6, accuracyM: 20, at: 1000 })
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it('descarta una lectura más vieja que la que tiene', () => {
    reportPosition({ lat: -33.04, lon: -71.6, accuracyM: 20, at: 2000 })
    reportPosition({ lat: -33.5, lon: -71.9, accuracyM: 20, at: 1000 })
    expect(getLastPosition()?.at).toBe(2000)
  })

  it('ignora coordenadas que no son números', () => {
    reportPosition({ lat: Number.NaN, lon: -71.6, accuracyM: null, at: 1000 })
    expect(getLastPosition()).toBeNull()
  })

  it('deja de avisar al desuscribirse', () => {
    const listener = vi.fn()
    const off = subscribePosition(listener)
    off()
    reportPosition({ lat: -33.04, lon: -71.6, accuracyM: 20, at: 1000 })
    expect(listener).not.toHaveBeenCalled()
  })
})
