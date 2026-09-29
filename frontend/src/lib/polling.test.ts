import { afterEach, describe, expect, it, vi } from 'vitest'
import { parseRetryAfter } from '@/api/client'
import { POLL_BACKOFF_MAX_MS, pollEvery } from './polling'

function query(status: string, errorUpdatedAt = 0) {
  return { state: { status, errorUpdatedAt } }
}

describe('pollEvery', () => {
  afterEach(() => vi.restoreAllMocks())

  it('sana, devuelve la cadencia base exacta', () => {
    const interval = pollEvery(60_000)
    expect(interval(query('success'))).toBe(60_000)
    expect(interval(query('pending'))).toBe(60_000)
  })

  it('duplica con cada fallo nuevo y se detiene en el tope', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5) // jitter neutro
    const interval = pollEvery(60_000)
    const q = query('error', 1)
    expect(interval(q)).toBe(60_000)
    q.state.errorUpdatedAt = 2
    expect(interval(q)).toBe(120_000)
    q.state.errorUpdatedAt = 3
    expect(interval(q)).toBe(240_000)
    q.state.errorUpdatedAt = 4
    expect(interval(q)).toBe(POLL_BACKOFF_MAX_MS)
  })

  it('reevaluar sin un fallo nuevo no cambia el valor: no reinicia el temporizador', () => {
    const interval = pollEvery(60_000)
    const q = query('error', 10)
    const first = interval(q)
    for (let i = 0; i < 20; i += 1) expect(interval(q)).toBe(first)
  })

  it('el jitter queda dentro de ±15 %', () => {
    for (const r of [0, 0.999]) {
      vi.spyOn(Math, 'random').mockReturnValue(r)
      const value = pollEvery(100_000)(query('error', 1))
      expect(value).toBeGreaterThanOrEqual(85_000)
      expect(value).toBeLessThanOrEqual(115_000)
      vi.restoreAllMocks()
    }
  })

  it('el primer éxito devuelve la cadencia base y reinicia la racha', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5)
    const interval = pollEvery(60_000)
    const q = query('error', 1)
    interval(q)
    q.state.errorUpdatedAt = 2
    expect(interval(q)).toBe(120_000)

    q.state.status = 'success'
    expect(interval(q)).toBe(60_000)

    q.state.status = 'error'
    q.state.errorUpdatedAt = 3
    expect(interval(q)).toBe(60_000)
  })
})

describe('parseRetryAfter', () => {
  it('segundos', () => {
    expect(parseRetryAfter('120')).toBe(120_000)
  })

  it('fecha HTTP', () => {
    const now = Date.parse('2026-09-29T12:00:00Z')
    expect(parseRetryAfter('Tue, 29 Sep 2026 12:00:30 GMT', now)).toBe(30_000)
  })

  it('ausente o ilegible: null, y manda el backoff propio', () => {
    expect(parseRetryAfter(null)).toBeNull()
    expect(parseRetryAfter('pronto')).toBeNull()
    expect(parseRetryAfter('')).toBeNull()
  })
})
