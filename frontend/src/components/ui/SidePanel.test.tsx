/**
 * Tests del panel de filtros colapsable.
 *
 * Fijan las tres cosas que se pueden romper sin que nadie lo note:
 *
 *   1. que la animación siga siendo `transform` y no `width` ni `display`;
 *   2. que el panel oculto quede fuera del alcance del teclado;
 *   3. que la pestaña siga siendo operable cuando todo lo demás está afuera.
 *
 * Son tests de comportamiento y de DOM, no de píxeles: jsdom no calcula layout.
 * La comprobación geométrica del desplazamiento vive aparte, en
 * `collapse-geometry.test.ts`.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DEFAULT_LAYER_VISIBILITY, DEFAULT_PROVIDER_VISIBILITY, SidePanel } from './SidePanel'
import { emptyByLayer, makeIncident } from '@/test/fixtures'

function renderPanel() {
  const onChange = vi.fn()
  render(
    <SidePanel
      visibility={DEFAULT_LAYER_VISIBILITY}
      onChange={onChange}
      counts={{ fire: 2, traffic: 1, power: 3, otros: 0, seismic: 4 }}
      incidentsByLayer={{ ...emptyByLayer, fire: [makeIncident()] }}
      seismicEvents={[]}
      selectedCode={null}
      selectedUsgsId={null}
      onFocusIncident={vi.fn()}
      onFocusSeismic={vi.fn()}
      seismicFilter="relevant"
      onSeismicFilterChange={vi.fn()}
      providers={DEFAULT_PROVIDER_VISIBILITY}
      onProvidersChange={vi.fn()}
    />,
  )

  const tab = screen.getByRole('button', { name: /filtros del mapa/i })
  const panel = document.getElementById('map-layer-panel') as HTMLElement
  const slider = panel.parentElement as HTMLElement
  return { tab, panel, slider, onChange }
}

describe('LayerToggles — panel colapsable', () => {
  it('arranca abierto', () => {
    const { tab, panel, slider } = renderPanel()

    expect(tab).toHaveAttribute('aria-expanded', 'true')
    expect(slider.className).toContain('translate-x-0')
    expect(panel).not.toHaveAttribute('inert')
  })

  it('se cierra al pulsar la pestaña y se vuelve a abrir', async () => {
    const user = userEvent.setup()
    const { tab, slider } = renderPanel()

    await user.click(tab)
    expect(tab).toHaveAttribute('aria-expanded', 'false')
    expect(slider.className).toContain('translate-x-[calc(100%+0.75rem)]')

    await user.click(tab)
    expect(tab).toHaveAttribute('aria-expanded', 'true')
    expect(slider.className).toContain('translate-x-0')
  })

  it('anima con transform y nunca con display ni width', async () => {
    const user = userEvent.setup()
    const { tab, slider } = renderPanel()

    const assertTransformOnly = () => {
      expect(slider.className).toContain('transition-transform')
      // Un `hidden` o un `w-0` delatarían que alguien cambió de técnica y
      // sacrificó la fluidez de la transición.
      expect(slider.className).not.toMatch(/\bhidden\b/)
      expect(slider.className).not.toMatch(/\bw-0\b/)
      expect(slider.style.display).not.toBe('none')
    }

    assertTransformOnly()
    await user.click(tab)
    assertTransformOnly()
  })

  it('mantiene el panel en el DOM al cerrarse', async () => {
    const user = userEvent.setup()
    const { tab, panel } = renderPanel()

    await user.click(tab)
    // Sigue existiendo: es lo que permite animarlo en vez de hacerlo aparecer.
    expect(panel).toBeInTheDocument()
    expect(panel.querySelector('fieldset')).toBeTruthy()
  })

  it('saca el contenido oculto del alcance del teclado y del lector', async () => {
    const user = userEvent.setup()
    const { tab, panel } = renderPanel()

    await user.click(tab)
    expect(panel).toHaveAttribute('inert')
    expect(panel).toHaveAttribute('aria-hidden', 'true')
  })

  it('deja la pestaña operable con el panel cerrado', async () => {
    const user = userEvent.setup()
    const { tab, panel } = renderPanel()

    await user.click(tab)
    // La pestaña es hermana del panel, no descendiente: `inert` no la alcanza.
    expect(panel.contains(tab)).toBe(false)
    expect(tab).not.toHaveAttribute('inert')
    expect(tab).toHaveAccessibleName(/mostrar filtros/i)
  })

  it('gira el chevron 180° sólo cuando está cerrado', async () => {
    const user = userEvent.setup()
    const { tab } = renderPanel()

    const chevron = () => tab.querySelector('svg') as SVGElement
    expect(chevron().getAttribute('class')).not.toContain('rotate-180')

    await user.click(tab)
    expect(chevron().getAttribute('class')).toContain('rotate-180')
  })

  it('describe la acción en el nombre accesible, no sólo en el ícono', async () => {
    const user = userEvent.setup()
    const { tab } = renderPanel()

    expect(tab).toHaveAccessibleName(/ocultar filtros/i)
    await user.click(tab)
    expect(tab).toHaveAccessibleName(/mostrar filtros/i)
  })

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
      /cortes de suministro/i,
      /otras emergencias/i,
      /sismos/i,
    ]) {
      expect(screen.getByRole('checkbox', { name })).toBeInTheDocument()
    }
  })
})
