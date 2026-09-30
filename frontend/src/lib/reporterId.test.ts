import { afterEach, describe, expect, it, vi } from 'vitest'
import { reporterId } from './reporterId'

describe('reporterId', () => {
  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('es estable y cumple el formato que acepta el backend', () => {
    const primero = reporterId()
    expect(primero).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
    expect(reporterId()).toBe(primero)
  })

  it('reemplaza un valor guardado que no cumple el formato', () => {
    window.localStorage.setItem('alertav:reporter-id', '<script>')
    expect(reporterId()).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
  })

  it('sin localStorage usa uno en memoria, también estable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('bloqueado')
    })
    const a = reporterId()
    expect(a).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
    expect(reporterId()).toBe(a)
  })
})
