/**
 * Tests de las capas de emergencia (la pestaña «Capas» de la columna).
 *
 * El panel flotante de la derecha y su pestaña colapsable se fueron con la
 * columna única (`components/shell`); lo que queda son las filas: casilla,
 * contador, salud, empresas y la lista de cada capa.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DEFAULT_LAYER_VISIBILITY, DEFAULT_PROVIDER_VISIBILITY, IncidentFilters } from './SidePanel'
import { emptyByLayer, makeIncident, makeWaterCut } from '@/test/fixtures'
import type { WaterPanel } from './SidePanel'

function renderPanel(water: WaterPanel | null = null) {
  const onChange = vi.fn()
  render(
    <IncidentFilters
      visibility={DEFAULT_LAYER_VISIBILITY}
      onChange={onChange}
      counts={{ fire: 2, traffic: 1, power: 3, water: water?.cuts.length ?? 0, otros: 0, seismic: 4 }}
      water={water}
      incidentsByLayer={{ ...emptyByLayer, fire: [makeIncident()] }}
      seismicEvents={[]}
      onFocusIncident={vi.fn()}
      onFocusSeismic={vi.fn()}
      seismicFilter="relevant"
      onSeismicFilterChange={vi.fn()}
      providers={DEFAULT_PROVIDER_VISIBILITY}
      onProvidersChange={vi.fn()}
    />,
  )

  return { onChange }
}

describe('capas de emergencia', () => {
  it('no interfiere con los filtros: la casilla sigue funcionando', async () => {
    const user = userEvent.setup()
    const { onChange } = renderPanel()

    await user.click(screen.getByRole('checkbox', { name: /incendios/i }))
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ fire: false }),
    )
  })
})

/**
 * Frontera con el controlador de referencia.
 *
 * Las capas de amenaza y lluvia se mudaron a `ReferenceDock`. Este bloque
 * impide que vuelvan por descuido: convivir en el mismo panel era lo que
 * obligaba a un modelo probabilístico a parecerse a un incidente en curso.
 */
describe('LayerToggles — sólo capas de emergencia', () => {
  it('no queda ningún interruptor de capa de referencia', () => {
    renderPanel()

    // Las capas de emergencia son CASILLAS; las de referencia, interruptores.
    // Cero interruptores es la forma más directa de comprobar la separación.
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
    expect(screen.queryByText(/amenaza sísmica/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/lluvia pronosticada/i)).not.toBeInTheDocument()
  })

  it('conserva las cinco capas de emergencia', () => {
    renderPanel()

    for (const name of [
      /incendios/i,
      /accidentes viales/i,
      /cortes de luz/i,
      /otras emergencias/i,
      /sismos/i,
    ]) {
      expect(screen.getByRole('checkbox', { name })).toBeInTheDocument()
    }
  })
})

describe('fila de cortes de agua', () => {
  function water(over: Partial<WaterPanel> = {}): WaterPanel {
    return {
      cuts: [
        makeWaterCut(),
        makeWaterCut({ id: 'q', comuna: 'Quilpué', programado: true, coordinates: null }),
      ],
      status: 'ok',
      detail: null,
      onFocus: vi.fn(),
      ...over,
    }
  }

  it('no existe mientras el backend no haya leído a Esval', () => {
    renderPanel(null)
    expect(screen.queryByRole('checkbox', { name: /cortes de agua/i })).not.toBeInTheDocument()
  })

  it('aparece junto a la luz, con su contador', () => {
    renderPanel(water())
    // Entre la luz (con sus empresas) y «Otras emergencias».
    const casillas = screen.getAllByRole('checkbox')
    const luz = casillas.indexOf(screen.getByRole('checkbox', { name: /cortes de luz/i }))
    const agua = casillas.indexOf(screen.getByRole('checkbox', { name: /cortes de agua/i }))
    const otras = casillas.indexOf(screen.getByRole('checkbox', { name: /otras emergencias/i }))
    expect(luz).toBeLessThan(agua)
    expect(agua).toBeLessThan(otras)
    expect(screen.getByRole('button', { name: /ver los 2 de cortes de agua/i })).toBeInTheDocument()
  })

  it('la lista dice comuna, calles y si no está en el mapa; tocar uno lo enfoca', async () => {
    const user = userEvent.setup()
    const panel = water()
    renderPanel(panel)

    await user.click(screen.getByRole('button', { name: /ver los 2 de cortes de agua/i }))
    expect(screen.getByText('Viña del Mar')).toBeInTheDocument()
    expect(screen.getByText(/· emergencia/)).toBeInTheDocument()
    expect(screen.getByText(/sin ubicación en el mapa/)).toBeInTheDocument()

    await user.click(screen.getByText('Quilpué'))
    expect(panel.onFocus).toHaveBeenCalledWith(expect.objectContaining({ id: 'q' }))
  })

  it('un cero con la fuente caída avisa que no se sabe', () => {
    renderPanel(water({ cuts: [], status: 'failing', detail: 'esval vía proxy-cl: sin respuesta' }))
    expect(screen.getByRole('checkbox', { name: /cortes de agua/i })).toBeInTheDocument()
    expect(screen.getByTitle(/esval vía proxy-cl/)).toBeInTheDocument()
  })
})
