/**
 * El enlace de cada señal correlacionada.
 *
 * Esto cubre un borde que termina en un `href` del navegador de un usuario. El
 * backend ya valida el esquema (`services/source_links.py` y su suite), así que
 * lo que se prueba acá es lo OTRO: que el `<a>` salga con las protecciones que
 * el esquema no da, que sin enlace no se pinte uno vacío, y que el nombre del
 * medio reemplace a la banda en vez de repetirla.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { IncidentEventLink } from '@/api/types'
import { makeIncident } from '@/test/fixtures'
import { IncidentSheet } from './IncidentSheet'

// El detalle llega por `useIncidentDetail`; acá interesa el render, no la red.
const señales: IncidentEventLink[] = []
vi.mock('@/hooks/useIncidentDetail', () => ({
  useIncidentDetail: () => ({
    data: { ...makeIncident(), events: señales },
    isLoading: false,
  }),
}))

function makeLink(over: Partial<IncidentEventLink> = {}): IncidentEventLink {
  return {
    raw_event_id: 14155,
    public_id: '8246e15b-c083-4d12-858b-09dfaffa26c0',
    source: 'media',
    type: 'accident',
    timestamp: '2026-09-02T19:17:21Z',
    confidence: 0.6,
    text: 'Bus pierde sus dos ruedas delanteras en Limache',
    lat: -33.0089828,
    lon: -71.2661868,
    link_method: 'spatial',
    link_confidence: 1,
    distance_m: 0,
    matched_commune: null,
    note: null,
    source_url: 'https://puranoticia.cl/nota/bus-limache',
    source_label: 'Pura Noticia',
    ...over,
  }
}

function montar(link: IncidentEventLink) {
  señales.splice(0, señales.length, link)
  render(<IncidentSheet incident={makeIncident({ type: 'accident' })} onClose={vi.fn()} />)
}

describe('señales correlacionadas', () => {
  it('enlaza el titular al medio que lo publicó', () => {
    montar(makeLink())

    const enlace = screen.getByRole('link', { name: /Bus pierde sus dos ruedas/ })
    expect(enlace).toHaveAttribute('href', 'https://puranoticia.cl/nota/bus-limache')
  })

  it('abre en pestaña nueva sin entregar el opener ni el referer', () => {
    montar(makeLink())

    const enlace = screen.getByRole('link', { name: /Bus pierde/ })
    // `noopener` impide que el destino manipule `window.opener`; `noreferrer`
    // que reciba de qué incidente salió. Validar el esquema en el backend no
    // cubre ninguna de las dos: son problemas distintos.
    expect(enlace).toHaveAttribute('rel', expect.stringContaining('noopener'))
    expect(enlace).toHaveAttribute('rel', expect.stringContaining('noreferrer'))
    expect(enlace).toHaveAttribute('target', '_blank')
  })

  it('avisa al lector de pantalla que el enlace se va a otra pestaña', () => {
    montar(makeLink())

    expect(
      screen.getByRole('link', { name: /abre en una pestaña nueva/i }),
    ).toBeInTheDocument()
  })

  it('sin URL muestra el texto plano y NO un enlace', () => {
    // El caso de Chilquinta y el del `guid` numérico de un despacho: el backend
    // manda `null` y el panel no puede inventar un destino.
    montar(makeLink({ source_url: null, source_label: null, source: 'chilquinta' }))

    expect(screen.queryByRole('link', { name: /Bus pierde/ })).toBeNull()
    expect(screen.getByText(/Bus pierde sus dos ruedas/)).toBeInTheDocument()
  })

  it('muestra el nombre del medio en vez de repetir la banda', () => {
    // «Prensa» ya está en el chip de Fuentes. Repetirlo por señal gasta la
    // línea sin agregar nada; «Pura Noticia» sí dice algo nuevo.
    montar(makeLink())

    expect(screen.getByText('Pura Noticia')).toBeInTheDocument()
  })

  it('sin nombre propio cae a la banda y no deja el hueco', () => {
    montar(makeLink({ source_label: null, source: 'chilquinta', source_url: null }))

    expect(screen.getByText('Chilquinta')).toBeInTheDocument()
  })

  it('una señal sin texto pero con URL igual ofrece un enlace con etiqueta', () => {
    // Un `<a>` vacío es invisible para el puntero e ilegible para el lector.
    montar(makeLink({ text: null }))

    expect(screen.getByRole('link', { name: /Ver publicación/ })).toHaveAttribute(
      'href',
      'https://puranoticia.cl/nota/bus-limache',
    )
  })
})
