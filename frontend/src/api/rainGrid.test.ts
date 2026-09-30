import { describe, expect, it, vi } from 'vitest'
import { parseRainGrid } from './rainGrid'

const OK = {
  grilla: {
    generado_en: '2026-09-30T14:00:00Z',
    modelo: 'best_match',
    horas: 24,
    paso: 0.15,
    oeste: -72.3,
    norte: -31.9,
    nx: 2,
    ny: 2,
    valores: [0, 1.5, null, 4],
  },
  fuente: { estado: 'ok', ultima_lectura: '2026-09-30T14:00:00Z', detalle: null },
}

describe('grilla de lluvia', () => {
  it('lee la grilla', () => {
    const { grid, state } = parseRainGrid(OK)
    expect(state).toBe('ok')
    expect(grid).toMatchObject({ nx: 2, ny: 2, step: 0.15, west: -72.3, north: -31.9 })
    expect(grid?.values).toEqual([0, 1.5, null, 4])
  })

  it('sin lectura todavía', () => {
    expect(parseRainGrid({ grilla: null, fuente: { estado: 'never' } })).toEqual({
      grid: null,
      state: 'never',
    })
  })

  it('una grilla que no cuadra se descarta con un solo aviso', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const broken = { ...OK, grilla: { ...OK.grilla, valores: [1, 2, 3] } }
    expect(parseRainGrid(broken).grid).toBeNull()
    expect(warn).toHaveBeenCalledOnce()
    warn.mockRestore()
  })

  it('un estado desconocido se lee como «nunca»', () => {
    expect(parseRainGrid({ fuente: { estado: 'raro' } }).state).toBe('never')
  })
})
