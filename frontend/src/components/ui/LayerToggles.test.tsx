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
import { DEFAULT_LAYER_VISIBILITY, DEFAULT_PROVIDER_VISIBILITY, LayerToggles } from './LayerToggles'
import type { RainStatus } from '@/hooks/useRainLayer'
import { emptyByLayer, makeIncident } from '@/test/fixtures'

/** Sobrescritura del estado de la capa de lluvia; el resto son valores neutros. */
type RainOverride = Partial<{
  enabled: boolean
  status: RainStatus
  count: number
  riskCount: number
}>

function renderPanel(rain: RainOverride = {}) {
  const onChange = vi.fn()
  const onHazardToggle = vi.fn()
  const onRainToggle = vi.fn()
  render(
    <LayerToggles
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
      hazardEnabled={false}
      hazardStatus="idle"
      onHazardToggle={onHazardToggle}
      onHazardRetry={vi.fn()}
      rainEnabled={rain.enabled ?? false}
      rainStatus={rain.status ?? 'idle'}
      rainCount={rain.count ?? 0}
      rainRiskCount={rain.riskCount ?? 0}
      onRainToggle={onRainToggle}
      onRainRetry={vi.fn()}
      theme="light"
    />,
  )

  const tab = screen.getByRole('button', { name: /filtros del mapa/i })
  const panel = document.getElementById('map-layer-panel') as HTMLElement
  const slider = panel.parentElement as HTMLElement
  return { tab, panel, slider, onChange, onHazardToggle, onRainToggle }
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
 * Interruptor de lluvia.
 *
 * El test que más importa es el del estado vacío. Una comuna ausente del GeoJSON
 * significa "comuna seca", así que una colección vacía es una respuesta CORRECTA
 * y, en verano, la respuesta normal durante semanas. Si alguien tratara ese caso
 * como un fallo, la app pasaría media temporada diciendo "sin datos" sobre un
 * backend que responde perfecto.
 */
describe('LayerToggles — lluvia pronosticada', () => {
  const findSwitch = () => screen.getByRole('switch', { name: /lluvia pronosticada/i })

  it('arranca apagado: la llamada a la API no ocurre sin un gesto del usuario', () => {
    renderPanel()
    expect(findSwitch()).toHaveAttribute('aria-checked', 'false')
  })

  it('avisa al encenderse, que es lo que dispara la carga diferida', async () => {
    const user = userEvent.setup()
    const { onRainToggle } = renderPanel()

    await user.click(findSwitch())
    expect(onRainToggle).toHaveBeenCalledTimes(1)
  })

  it('el estado vacío dice "sin lluvia", nunca "sin datos"', () => {
    renderPanel({ enabled: true, status: 'empty', count: 0, riskCount: 0 })

    expect(screen.getByText(/sin lluvia pronosticada/i)).toBeInTheDocument()
    expect(screen.queryByText(/sin datos/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/no se pudo cargar/i)).not.toBeInTheDocument()
    // Y no ofrece reintentar: no hay nada que reintentar.
    expect(screen.queryByRole('button', { name: /reintentar/i })).not.toBeInTheDocument()
  })

  it('distingue el error del estado seco', () => {
    renderPanel({ enabled: true, status: 'error' })

    expect(screen.getByText(/no se pudo cargar/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reintentar/i })).toBeInTheDocument()
  })

  it('cuenta las comunas en riesgo cuando las hay', () => {
    renderPanel({ enabled: true, status: 'ready', count: 5, riskCount: 2 })
    expect(screen.getByText(/5 comunas · 2 con riesgo/i)).toBeInTheDocument()
  })

  it('nunca llama "inundación" a un pronóstico', () => {
    renderPanel({ enabled: true, status: 'ready', count: 3, riskCount: 1 })

    // El hand-off del backend es explícito: `riesgo_inundacion: true` es un
    // umbral cruzado por un modelo, no una inundación, y tampoco una alerta
    // oficial — esas las declara SENAPRED y llegan por otra vía.
    expect(screen.getByText(/SENAPRED/)).toBeInTheDocument()
    expect(screen.getByTitle(/riesgo de inundación pronosticado/i)).toBeInTheDocument()
    // Nada en el panel afirma que HAY una inundación.
    expect(screen.queryByText(/^\s*inundaci[óo]n\s*$/i)).not.toBeInTheDocument()
  })
})
