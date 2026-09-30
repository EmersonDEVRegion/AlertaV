/**
 * La columna única (escritorio) y la hoja inferior (teléfono).
 *
 * Es el mismo contenido en dos contenedores: se fija lo que hace cada pestaña,
 * que la ficha reemplace a la lista en el mismo lugar, y las alturas de la
 * hoja. jsdom no calcula layout: el arrastre se prueba en Chromium.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { DEFAULT_LAYER_VISIBILITY, DEFAULT_PROVIDER_VISIBILITY } from '@/components/ui/SidePanel'
import { emptyByLayer, makeIncident, makeWaterCut } from '@/test/fixtures'
import { resetSelection, selectWaterCut } from '@/lib/selectionStore'
import { getSheetSnap, resetSheet } from '@/lib/sheetStore'
import { BottomSheet } from './BottomSheet'
import { DesktopColumn } from './DesktopColumn'
import type { ExplorePanelProps } from './ExplorePanel'

const FIRE = makeIncident({
  code: 'INC-2026-00681',
  type: 'structural_fire',
  title: 'Incendio estructural — Concón',
})
const CUT = makeWaterCut({ comuna: 'Zapallar' })

function props(over: Partial<ExplorePanelProps> = {}): ExplorePanelProps {
  return {
    incidentCount: 1,
    incidents: {
      visibility: DEFAULT_LAYER_VISIBILITY,
      onChange: vi.fn(),
      counts: { fire: 1, traffic: 0, power: 0, water: 1, otros: 0, seismic: 0 },
      incidentsByLayer: { ...emptyByLayer, fire: [FIRE] },
      seismicEvents: [],
      onFocusIncident: vi.fn(),
      onFocusSeismic: vi.fn(),
      seismicFilter: 'relevant',
      onSeismicFilterChange: vi.fn(),
      providers: DEFAULT_PROVIDER_VISIBILITY,
      onProvidersChange: vi.fn(),
    },
    reference: {
      hazardEnabled: false,
      hazardStatus: 'idle',
      hazardError: null,
      onHazardToggle: vi.fn(),
      onHazardRetry: vi.fn(),
      closureEnabled: false,
      closureStatus: 'idle',
      closureCount: 0,
      closureCutCount: 0,
      onClosureToggle: vi.fn(),
      onClosureRetry: vi.fn(),
      theme: 'light',
    },
    history: {
      history: [FIRE],
      onMapCodes: new Set([FIRE.code]),
      onFocus: vi.fn(),
      ready: true,
    },
    selection: { incidents: [FIRE], seismic: [], waterCuts: [CUT] },
    ...over,
  }
}

function withQuery(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, enabled: false } } })
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>
}

afterEach(() => {
  resetSelection()
  resetSheet()
})

describe('columna de escritorio', () => {
  it('resume sin abrir nada y arranca en el historial', () => {
    render(withQuery(<DesktopColumn {...props()} />))

    expect(screen.getByRole('heading', { name: /1 en curso/i })).toBeInTheDocument()
    expect(screen.getByText(/en 24 h/)).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /historial/i })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByText('Incendio estructural — Concón')).toBeInTheDocument()
  })

  it('las capas de referencia van arriba del historial, plegadas', () => {
    render(withQuery(<DesktopColumn {...props()} />))
    const dock = screen.getByRole('button', { name: /capas de referencia/i })
    expect(dock).toHaveAttribute('aria-expanded', 'false')
  })

  it('cada pestaña abre lo suyo', async () => {
    const user = userEvent.setup()
    render(withQuery(<DesktopColumn {...props()} />))

    await user.click(screen.getByRole('tab', { name: /capas/i }))
    expect(screen.getByRole('checkbox', { name: /incendios/i })).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: /leyenda/i }))
    expect(screen.getByText(/el color mide evidencia/i)).toBeInTheDocument()
  })

  it('la ficha reemplaza a la lista en el mismo lugar, y cerrarla la devuelve', async () => {
    const user = userEvent.setup()
    render(withQuery(<DesktopColumn {...props()} />))

    act(() => selectWaterCut(CUT.id))
    // Incrustada no es un diálogo flotante: es una región de la columna.
    expect(
      await screen.findByRole('region', { name: /corte de agua de esval en zapallar/i }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /cerrar/i }))
    expect(screen.getByRole('tablist')).toBeInTheDocument()
  })
})

describe('hoja inferior del teléfono', () => {
  const sheet = () => screen.getByRole('complementary', { name: /emergencias e historial/i })

  it('arranca asomada: el mapa es el contenido', () => {
    render(withQuery(<BottomSheet {...props()} />))
    expect(sheet()).toHaveAttribute('data-snap', 'peek')
  })

  it('tocar una pestaña la sube a media pantalla', async () => {
    const user = userEvent.setup()
    render(withQuery(<BottomSheet {...props()} />))

    await user.click(screen.getByRole('tab', { name: /capas/i }))
    expect(getSheetSnap()).toBe('half')
    expect(sheet()).toHaveAttribute('data-snap', 'half')
  })

  it('el asa alterna las tres alturas con el teclado', async () => {
    const user = userEvent.setup()
    render(withQuery(<BottomSheet {...props()} />))
    const grip = screen.getByRole('button', { name: /expandir la lista$/i })

    grip.focus()
    await user.keyboard('{Enter}')
    expect(getSheetSnap()).toBe('half')
    await user.keyboard('{Enter}')
    expect(getSheetSnap()).toBe('full')
    await user.keyboard('{Enter}')
    expect(getSheetSnap()).toBe('peek')
  })

  it('abrir una ficha desde el mapa la sube, porque la ficha vive adentro', async () => {
    render(withQuery(<BottomSheet {...props()} />))
    act(() => selectWaterCut(CUT.id))
    expect(getSheetSnap()).toBe('half')
    expect(
      await screen.findByRole('region', { name: /corte de agua de esval en zapallar/i }),
    ).toBeInTheDocument()
  })
})
