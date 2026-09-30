import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import { readableError } from './useCitizenReport'

const err = (status: number, body: unknown = undefined, retryAfterMs: number | null = null) =>
  new ApiError('x', status, '/events/citizen-report', body, retryAfterMs)

describe('readableError del reporte ciudadano', () => {
  it('muestra tal cual el 422 de geocerca o precisión, que llega como texto', () => {
    const detalle = 'Tu ubicación está fuera de la Región de Valparaíso.'
    expect(readableError(err(422, { detail: detalle }))).toBe(detalle)
  })

  it('sigue leyendo el 422 de validación de FastAPI', () => {
    expect(readableError(err(422, { detail: [{ msg: 'campo requerido' }] }))).toContain(
      'campo requerido',
    )
  })

  it('explica el 403 de Turnstile', () => {
    expect(readableError(err(403))).toContain('verificar')
  })

  it('dice cuántos minutos esperar si el servidor lo informó', () => {
    expect(readableError(err(429, undefined, 301_000))).toContain('6 min')
    expect(readableError(err(429))).toContain('Espera unos minutos')
  })
})
