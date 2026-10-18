import { afterEach, describe, expect, it } from 'vitest'
import { act, render, renderHook, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  EMPTY_TRANSITO,
  boundsOfTransito,
  parseTransito,
  type TransitoFeature,
  type TransitoSubtipo,
} from '@/api/transito'
import { subtiposFilter, transitoSpecs } from '@/components/map/transitoLayers'
import { TransitoAhoraView } from '@/components/shell/TransitoAhora'
import { ordenarTransito, vigentesTransito } from '@/hooks/useTransito'
import { parseDeepLink } from '@/lib/push'
import { resetSections } from '@/lib/sectionStore'
import { getSheetSnap, resetSheet, setSheetSnap } from '@/lib/sheetStore'
import {
  resetTransito,
  setTransitoEnabled,
  showTransitoOnMap,
  toggleTransitoSubtipo,
  useTransitoState,
} from '@/lib/transitoStore'

const ID = '0b6e1c2d-1111-2222-3333-444455556666'
const AHORA = Date.parse('2026-10-18T18:00:00Z')

function crudo(subtipo: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'Feature',
    geometry: {
      type: 'LineString',
      coordinates: [
        [-71.55201, -33.02607],
        [-71.56155, -33.02482],
      ],
    },
    properties: {
      public_id: `${subtipo}-${ID.slice(subtipo.length + 1)}`,
      subtipo,
      nombre: 'Congestión',
      titulo: `Titulo de ${subtipo}`,
      comuna: 'Viña del Mar',
      url: 'https://x.com/TTIValparaiso/status/1',
      text: 'aviso',
      timestamp: '2026-10-18T17:40:00+00:00',
      vence: '2026-10-18T19:10:00+00:00',
      lat: -33.0256,
      lon: -71.5558,
      ...extra,
    },
  }
}

function feature(subtipo: TransitoSubtipo, extra: Record<string, unknown> = {}): TransitoFeature {
  return parseTransito({ features: [crudo(subtipo, extra)] }).features[0]!
}

afterEach(() => {
  resetTransito()
  resetSections()
  resetSheet()
})

describe('respuesta del backend', () => {
  it('se queda con lo válido y descarta lo que no conoce', () => {
    const data = parseTransito({
      features: [
        crudo('congestion'),
        crudo('choque'),
        { properties: { subtipo: 'lento' } },
        crudo('semaforo', { titulo: '' }),
        { ...crudo('marcha'), geometry: null },
      ],
    })
    expect(data.features.map((f) => f.properties.subtipo)).toEqual([
      'congestion',
      'semaforo',
      'marcha',
    ])
    expect(data.features[1]!.properties.titulo).toBe('Congestión')
    expect(data.features[2]!.geometry).toBeNull()
    expect(parseTransito(null)).toBe(EMPTY_TRANSITO)
  })

  it('la caja del tramo y la de un punto', () => {
    expect(boundsOfTransito(feature('congestion'))).toEqual([
      -71.56155, -33.02607, -71.55201, -33.02482,
    ])
    const punto = parseTransito({
      features: [{ ...crudo('semaforo'), geometry: { type: 'Point', coordinates: [-71.5, -33.0] } }],
    }).features[0]!
    expect(boundsOfTransito(punto)).toEqual([-71.5, -33.0, -71.5, -33.0])
  })
})

describe('vigencia y orden', () => {
  it('lo vencido con el reloj del teléfono no se muestra', () => {
    const data = parseTransito({
      features: [crudo('congestion'), crudo('lento', { vence: '2026-10-18T17:59:00+00:00' })],
    })
    expect(vigentesTransito(data, AHORA).features.map((f) => f.properties.subtipo)).toEqual([
      'congestion',
    ])
    expect(vigentesTransito(data, Date.parse('2026-10-18T17:00:00Z'))).toBe(data)
  })

  it('lo que cambia qué hacer arriba y la normalidad al final', () => {
    const orden = ordenarTransito([
      feature('normalidad'),
      feature('lento'),
      feature('marcha'),
      feature('congestion'),
    ]).map((f) => f.properties.subtipo)
    expect(orden).toEqual(['marcha', 'congestion', 'lento', 'normalidad'])
  })
})

describe('capa del mapa', () => {
  it('los chips ocultan subtipos y el foco resalta un tramo', () => {
    expect(subtiposFilter(['lento'])).toEqual([
      'in',
      ['get', 'subtipo'],
      ['literal', ['congestion', 'semaforo', 'pista_ocupada', 'marcha', 'normalidad']],
    ])
    const apagada = transitoSpecs('light', false, [], null)
    expect(apagada.line.layout?.visibility).toBe('none')
    expect(apagada.focus.filter).toEqual(['==', 1, 0])
    const foco = transitoSpecs('dark', true, [], ID)
    expect(JSON.stringify(foco.focus.filter)).toContain(ID)
  })

  it('el verde es de la normalidad', () => {
    const color = JSON.stringify(transitoSpecs('light', true, [], null).line.paint?.['line-color'])
    expect(color).toContain('"normalidad","#16a34a"')
  })
})

describe('estado de la capa', () => {
  it('ver en el mapa la enciende y vuelve a mostrar el subtipo oculto', () => {
    const { result } = renderHook(() => useTransitoState())
    act(() => toggleTransitoSubtipo('marcha'))
    expect(result.current.ocultos).toEqual(['marcha'])
    act(() => showTransitoOnMap(ID, 'marcha'))
    expect(result.current.enabled).toBe(true)
    expect(result.current.hasMounted).toBe(true)
    expect(result.current.ocultos).toEqual([])
    expect(result.current.focus?.id).toBe(ID)
    act(() => setTransitoEnabled(false))
    expect(result.current.focus).toBeNull()
    expect(result.current.hasMounted).toBe(true)
  })
})

describe('lista de «Ahora»', () => {
  it('sin nada vigente no se dibuja', () => {
    const { container } = render(<TransitoAhoraView status="empty" items={[]} now={AHORA} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('muestra «Marcha por …» y lleva el tramo al mapa', async () => {
    const user = userEvent.setup()
    setSheetSnap('half')
    const marcha = feature('marcha', { titulo: 'Marcha por Av. Pedro Montt', public_id: ID })
    render(
      <TransitoAhoraView status="ready" items={[marcha, feature('lento')]} now={AHORA} />,
    )
    await user.click(screen.getByRole('button', { name: /Tránsito · MTT/ }))
    await user.click(screen.getByRole('button', { name: /Marcha por Av\. Pedro Montt/ }))
    expect(screen.getByRole('link', { name: 'Ver el aviso del MTT' })).toHaveAttribute(
      'href',
      'https://x.com/TTIValparaiso/status/1',
    )
    await user.click(screen.getByRole('button', { name: 'Ver en el mapa' }))
    const { result } = renderHook(() => useTransitoState())
    expect(result.current.focus?.id).toBe(ID)
    expect(getSheetSnap()).toBe('peek')
  })

  it('lo que no se pudo ubicar se lista sin «Ver en el mapa»', async () => {
    const user = userEvent.setup()
    const perdido = parseTransito({
      features: [{ ...crudo('semaforo', { titulo: 'Semáforo apagado en Limache' }), geometry: null }],
    }).features[0]!
    render(<TransitoAhoraView status="ready" items={[perdido]} now={AHORA} />)
    await user.click(screen.getByRole('button', { name: /Tránsito · MTT/ }))
    await user.click(screen.getByRole('button', { name: /Semáforo apagado en Limache/ }))
    expect(screen.queryByRole('button', { name: 'Ver en el mapa' })).toBeNull()
    expect(screen.getByText('No pudimos ubicarlo en el mapa.')).toBeInTheDocument()
  })
})

describe('enlace de un aviso de tránsito', () => {
  it('lleva al tramo con su punto', () => {
    expect(parseDeepLink(`/?transito=${ID}&lat=-33.0257&lon=-71.5558`)).toEqual({
      kind: 'transito',
      id: ID,
      lat: -33.0257,
      lon: -71.5558,
    })
    expect(parseDeepLink('/?transito=nada&lat=1&lon=2')).toBeNull()
  })
})
