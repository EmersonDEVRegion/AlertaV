import { describe, expect, it, vi } from 'vitest'
import { shareIncident, shareUrl } from '@/lib/share'
import { parseDeepLink } from '@/lib/push'

const ORIGIN = 'https://alertav.vercel.app'

describe('shareUrl', () => {
  it('arma /i/<folio>', () => {
    expect(shareUrl('INC-2026-00142', ORIGIN)).toBe('https://alertav.vercel.app/i/INC-2026-00142')
  })
})

describe('shareIncident', () => {
  const input = { code: 'INC-2026-00142', title: 'Incendio — Viña del Mar' }

  it('usa la hoja nativa si existe', async () => {
    const share = vi.fn().mockResolvedValue(undefined)
    const writeText = vi.fn()
    const out = await shareIncident(input, { share, clipboard: { writeText } } as never, ORIGIN)
    expect(out).toBe('shared')
    expect(share).toHaveBeenCalledWith(
      expect.objectContaining({ url: 'https://alertav.vercel.app/i/INC-2026-00142' }),
    )
    expect(writeText).not.toHaveBeenCalled()
  })

  it('cerrar la hoja no es un error ni copia', async () => {
    const share = vi.fn().mockRejectedValue(new DOMException('x', 'AbortError'))
    const writeText = vi.fn()
    const out = await shareIncident(input, { share, clipboard: { writeText } } as never, ORIGIN)
    expect(out).toBe('cancelled')
    expect(writeText).not.toHaveBeenCalled()
  })

  it('sin hoja nativa copia el enlace', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    const out = await shareIncident(input, { clipboard: { writeText } } as never, ORIGIN)
    expect(out).toBe('copied')
    expect(writeText).toHaveBeenCalledWith('https://alertav.vercel.app/i/INC-2026-00142')
  })

  it('si tampoco se puede copiar, lo dice', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('no'))
    const out = await shareIncident(input, { clipboard: { writeText } } as never, ORIGIN)
    expect(out).toBe('failed')
  })
})

describe('parseDeepLink con la ruta /i/', () => {
  it('lee el folio de la ruta', () => {
    expect(parseDeepLink('https://alertav.vercel.app/i/INC-2026-00142')).toEqual({
      kind: 'incident',
      code: 'INC-2026-00142',
    })
  })

  it('acepta el folio en minúsculas', () => {
    expect(parseDeepLink('https://alertav.vercel.app/i/inc-2026-00142')).toEqual({
      kind: 'incident',
      code: 'INC-2026-00142',
    })
  })

  it('ignora rutas que no son un folio', () => {
    expect(parseDeepLink('https://alertav.vercel.app/i/hola')).toBeNull()
  })
})
