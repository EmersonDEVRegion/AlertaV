// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { parseNewsFeed } from './newsFeed'

function nota(overrides: Record<string, unknown> = {}) {
  return {
    id: 'n1',
    titular: 'Incendio afecta vivienda en cerro Placeres',
    bajada: null,
    medio: 'Pura Noticia',
    url: 'https://ejemplo.cl/nota',
    comuna: 'Valparaíso',
    tipo: 'structural_fire',
    publicada_en: '2026-10-06T13:00:00Z',
    hora_aproximada: false,
    detectada_en: '2026-10-06T13:20:00Z',
    ...overrides,
  }
}

describe('parseNewsFeed', () => {
  it('conserva una nota bien formada y la salud de la fuente', () => {
    const parsed = parseNewsFeed({
      horas: 24,
      items: [nota()],
      fuente: { collector: 'prensa_local', estado: 'ok' },
    })
    expect(parsed.items).toHaveLength(1)
    expect(parsed.items[0]!.medio).toBe('Pura Noticia')
    expect(parsed.fuente.estado).toBe('ok')
  })

  it('descarta las notas sin titular o sin fecha legible', () => {
    const parsed = parseNewsFeed({
      items: [nota({ id: 'a', titular: '' }), nota({ id: 'b', publicada_en: 'ayer' }), nota()],
    })
    expect(parsed.items.map((i) => i.id)).toEqual(['n1'])
  })

  it('no acepta enlaces que no sean http(s)', () => {
    const parsed = parseNewsFeed({ items: [nota({ url: 'javascript:alert(1)' })] })
    expect(parsed.items[0]!.url).toBeNull()
  })

  it('una respuesta rara es un feed vacío, no una excepción', () => {
    const parsed = parseNewsFeed('nada')
    expect(parsed.items).toEqual([])
    expect(parsed.fuente.collector).toBe('prensa_local')
    expect(parsed.fuente.estado).toBeUndefined()
  })
})
