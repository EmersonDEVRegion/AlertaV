/**
 * La pestaña «Capas»: el mosaico de emergencias, sus filtros y la leyenda.
 *
 * Lo que se fija acá es el contrato del rediseño: cada capa es un azulejo que
 * se enciende con un toque (no una casilla), los filtros finos sólo existen con
 * su capa encendida, un cero ciego se marca, y «Mostrar todo» deshace cualquier
 * filtro olvidado.
 */
import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DEFAULT_LAYER_VISIBILITY, DEFAULT_PROVIDER_VISIBILITY, type LayerVisibility } from '@/domain/layers'
import { resetSections } from '@/lib/sectionStore'
import { LayersTab, type EmergencyLayersProps, type WaterLayerStatus } from './LayersTab'
import type { ReferenceLayersProps } from './ReferenceLayers'

const REFERENCE: ReferenceLayersProps = {
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
  cuartelesEnabled: false,
  cuartelesStatus: 'idle',
  cuartelesCount: 0,
  cuartelesError: null,
  onCuartelesToggle: vi.fn(),
  onCuartelesRetry: vi.fn(),
  theme: 'light',
}

function renderTab(over: Partial<EmergencyLayersProps> = {}) {
  const props: EmergencyLayersProps = {
    visibility: DEFAULT_LAYER_VISIBILITY,
    onChange: vi.fn(),
    counts: { fire: 2, traffic: 1, power: 9, water: 0, otros: 0, seismic: 4 },
    seismicFilter: 'relevant',
    onSeismicFilterChange: vi.fn(),
    providers: DEFAULT_PROVIDER_VISIBILITY,
    onProvidersChange: vi.fn(),
    providerCounts: { chilquinta: 7, cge: 2 },
    water: null,
    confirmedOnly: false,
    onConfirmedOnlyChange: vi.fn(),
    ...over,
  }
  resetSections()
  render(<LayersTab emergencies={props} reference={REFERENCE} />)
  return props
}

const tile = (name: RegExp) => screen.getByRole('button', { name })
const emergencias = () => screen.getByRole('region', { name: 'Emergencias' })

describe('mosaico de emergencias', () => {
  it('cada capa es un azulejo encendible, no una casilla', () => {
    renderTab()
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
    for (const name of [/incendios/i, /accidentes viales/i, /cortes de luz/i, /otras emergencias/i, /sismos/i]) {
      expect(within(emergencias()).getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true')
    }
  })

  it('un toque apaga la capa y no toca las demás', async () => {
    const user = userEvent.setup()
    const { onChange } = renderTab()
    await user.click(tile(/incendios/i))
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_LAYER_VISIBILITY, fire: false })
  })

  it('apagada, el azulejo lo dice', () => {
    renderTab({ visibility: { ...DEFAULT_LAYER_VISIBILITY, traffic: false } })
    expect(tile(/accidentes viales/i)).toHaveAttribute('aria-pressed', 'false')
  })

  it('lleva el contador de lo que hay en el mapa', () => {
    renderTab()
    expect(tile(/cortes de luz/i)).toHaveTextContent('9')
  })

  it('ya no despliega una lista de incidentes', () => {
    renderTab()
    expect(screen.queryByRole('button', { name: /^ver los/i })).not.toBeInTheDocument()
  })
})

describe('cortes de agua', () => {
  const water = (over: Partial<WaterLayerStatus> = {}): WaterLayerStatus => ({
    status: 'ok',
    detail: null,
    ...over,
  })

  it('no hay azulejo mientras el backend no haya leído a Esval', () => {
    renderTab({ water: null })
    expect(screen.queryByRole('button', { name: /cortes de agua/i })).not.toBeInTheDocument()
  })

  it('va entre la luz y «Otras emergencias»', () => {
    renderTab({ water: water() })
    const names = within(emergencias())
      .getAllByRole('button', { pressed: true })
      .map((b) => b.textContent ?? '')
    const at = (label: string) => names.findIndex((n) => n.includes(label))
    expect(at('Cortes de luz')).toBeLessThan(at('Cortes de agua'))
    expect(at('Cortes de agua')).toBeLessThan(at('Otras emergencias'))
  })

  it('un cero con la fuente caída avisa que no se sabe', () => {
    renderTab({ water: water({ status: 'failing', detail: 'esval vía proxy-cl: sin respuesta' }) })
    expect(tile(/cortes de agua/i)).toHaveTextContent('Sin datos')
    expect(screen.getByTitle(/esval vía proxy-cl/)).toBeInTheDocument()
  })
})

describe('filtros finos', () => {
  it('las empresas sólo aparecen con los cortes de luz encendidos', () => {
    renderTab({ visibility: { ...DEFAULT_LAYER_VISIBILITY, power: false } })
    expect(screen.queryByRole('group', { name: 'Empresas' })).not.toBeInTheDocument()
  })

  it('cada empresa se apaga sola y lleva su número', async () => {
    const user = userEvent.setup()
    const { onProvidersChange } = renderTab()
    const cge = within(screen.getByRole('group', { name: 'Empresas' })).getByRole('button', { name: /cge/i })
    expect(cge).toHaveTextContent('2')
    await user.click(cge)
    expect(onProvidersChange).toHaveBeenCalledWith({ chilquinta: true, cge: false })
  })

  it('la relevancia de los sismos es una elección única', async () => {
    const user = userEvent.setup()
    const { onSeismicFilterChange } = renderTab()
    const group = screen.getByRole('radiogroup', { name: 'Sismos' })
    expect(within(group).getByRole('radio', { name: /relevantes/i })).toHaveAttribute('aria-checked', 'true')
    await user.click(within(group).getByRole('radio', { name: /microsismos/i }))
    expect(onSeismicFilterChange).toHaveBeenCalledWith('micro')
  })

  it('«sólo verificados» es una ficha y, encendida, explica qué esconde', async () => {
    const user = userEvent.setup()
    const { onConfirmedOnlyChange } = renderTab()
    await user.click(screen.getByRole('button', { name: /sólo verificados/i }))
    expect(onConfirmedOnlyChange).toHaveBeenCalledWith(true)
  })
})

describe('«Mostrar todo»', () => {
  it('no aparece si no hay nada oculto', () => {
    renderTab()
    expect(screen.queryByRole('button', { name: 'Mostrar todo' })).not.toBeInTheDocument()
  })

  it('deshace capas, empresas y verificados de una vez', async () => {
    const user = userEvent.setup()
    const visibility: LayerVisibility = { ...DEFAULT_LAYER_VISIBILITY, fire: false }
    const props = renderTab({
      visibility,
      providers: { chilquinta: true, cge: false },
      confirmedOnly: true,
    })
    expect(screen.getByText(/sólo lo que conaf/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Mostrar todo' }))
    expect(props.onChange).toHaveBeenCalledWith(DEFAULT_LAYER_VISIBILITY)
    expect(props.onProvidersChange).toHaveBeenCalledWith(DEFAULT_PROVIDER_VISIBILITY)
    expect(props.onConfirmedOnlyChange).toHaveBeenCalledWith(false)
  })
})

describe('grupos y leyenda', () => {
  it('emergencias y referencia van en grupos separados', () => {
    renderTab()
    expect(within(emergencias()).queryByRole('button', { name: /amenaza sísmica/i })).not.toBeInTheDocument()
    expect(
      within(screen.getByRole('region', { name: 'Referencia' })).getByRole('button', { name: /amenaza sísmica/i }),
    ).toBeInTheDocument()
  })

  it('la leyenda arranca plegada y se abre', async () => {
    const user = userEvent.setup()
    renderTab()
    expect(screen.queryByText(/el color mide evidencia/i)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /leyenda/i }))
    expect(screen.getByText(/el color mide evidencia/i)).toBeInTheDocument()
  })
})
