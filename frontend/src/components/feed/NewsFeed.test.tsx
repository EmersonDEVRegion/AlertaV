import { afterEach, describe, expect, it } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { NewsFeedItem } from '@/api/newsFeedTypes'
import { resetExplore, setExploreArea } from '@/lib/exploreStore'
import { NEWS_PREVIEW, NewsFeedView } from './NewsFeed'

function nota(id: string, overrides: Partial<NewsFeedItem> = {}): NewsFeedItem {
  return {
    id,
    titular: `Nota ${id}`,
    bajada: null,
    medio: 'Pura Noticia',
    url: `https://ejemplo.cl/${id}`,
    comuna: 'Valparaíso',
    tipo: 'structural_fire',
    publicada_en: new Date(Date.now() - 2 * 3_600_000).toISOString(),
    hora_aproximada: false,
    detectada_en: new Date().toISOString(),
    ...overrides,
  }
}

afterEach(() => resetExplore())

describe('NewsFeedView', () => {
  it('lista las notas como enlaces externos y dice que no van al mapa', () => {
    render(<NewsFeedView status="ready" items={[nota('a')]} />)
    const link = screen.getByRole('link', { name: /Nota a/ })
    expect(link).toHaveAttribute('href', 'https://ejemplo.cl/a')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
    expect(screen.getByText(/no se marcan en el mapa/i)).toBeInTheDocument()
    expect(screen.getByText(/Pura Noticia · Valparaíso/)).toBeInTheDocument()
  })

  it('marca la hora aproximada', () => {
    render(<NewsFeedView status="ready" items={[nota('a', { hora_aproximada: true })]} />)
    expect(screen.getByText(/^≈ /)).toBeInTheDocument()
  })

  it('muestra las primeras y deja ver el resto', async () => {
    const items = Array.from({ length: NEWS_PREVIEW + 2 }, (_, i) => nota(String(i)))
    render(<NewsFeedView status="ready" items={items} />)
    expect(screen.getAllByRole('link')).toHaveLength(NEWS_PREVIEW)
    await userEvent.click(screen.getByRole('button', { name: 'Ver 2 más' }))
    expect(screen.getAllByRole('link')).toHaveLength(NEWS_PREVIEW + 2)
  })

  it('con una comuna elegida, sólo las de esa comuna', () => {
    act(() => setExploreArea({ kind: 'commune', name: 'VIÑA DEL MAR' }))
    render(
      <NewsFeedView
        status="ready"
        items={[nota('a'), nota('b', { comuna: 'Viña del Mar' })]}
      />,
    )
    expect(screen.getAllByRole('link')).toHaveLength(1)
    expect(screen.getByRole('link', { name: /Nota b/ })).toBeInTheDocument()
  })

  it('distingue calma de ceguera', () => {
    const { rerender } = render(<NewsFeedView status="empty" items={[]} />)
    expect(screen.getByText(/Sin noticias de emergencias/)).toBeInTheDocument()
    rerender(<NewsFeedView status="blind" items={[]} />)
    expect(screen.getByText(/no está respondiendo/)).toBeInTheDocument()
  })

  it('un backend sin la ruta no muestra nada', () => {
    const { container } = render(<NewsFeedView status="unavailable" items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('se pliega', async () => {
    render(<NewsFeedView status="ready" items={[nota('a')]} />)
    await userEvent.click(screen.getByRole('button', { name: /Noticias/ }))
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
})
