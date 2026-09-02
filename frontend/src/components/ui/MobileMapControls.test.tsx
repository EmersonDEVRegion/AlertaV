/**
 * Controles del mapa en pantalla estrecha.
 *
 * El bug que este componente vino a cerrar no se veía en escritorio: los dos
 * paneles flotantes suman 488 px de cromo y en un teléfono se montaban uno
 * encima del otro. La garantía que lo cierra es de EXCLUSIÓN —nunca hay dos
 * paneles abiertos— y por eso es lo primero que se fija acá.
 *
 * jsdom no calcula layout, así que no se puede medir el solapamiento. Se
 * comprueba lo que sí lo hace imposible: que en el documento no exista más de
 * un panel a la vez.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MobileMapControls } from './MobileMapControls'
import { DEFAULT_LAYER_VISIBILITY, DEFAULT_PROVIDER_VISIBILITY } from './SidePanel'
import { emptyByLayer, makeIncident } from '@/test/fixtures'

function renderControls(
  over: { hazardEnabled?: boolean; closureEnabled?: boolean } = {},
) {
  const onHazardToggle = vi.fn()
  const onChange = vi.fn()

  render(
    <MobileMapControls
      incidentCount={3}
      incidents={{
        visibility: DEFAULT_LAYER_VISIBILITY,
        onChange,
        counts: { fire: 2, traffic: 1, power: 0, otros: 0, seismic: 0 },
        incidentsByLayer: { ...emptyByLayer, fire: [makeIncident()] },
        seismicEvents: [],
        selectedCode: null,
        selectedUsgsId: null,
        onFocusIncident: vi.fn(),
        onFocusSeismic: vi.fn(),
        seismicFilter: 'relevant',
        onSeismicFilterChange: vi.fn(),
        providers: DEFAULT_PROVIDER_VISIBILITY,
        onProvidersChange: vi.fn(),
      }}
      reference={{
        hazardEnabled: over.hazardEnabled ?? false,
        hazardStatus: 'idle',
        hazardError: null,
        onHazardToggle,
        onHazardRetry: vi.fn(),
        closureEnabled: over.closureEnabled ?? false,
        closureStatus: 'idle',
        closureCount: 0,
        closureCutCount: 0,
        onClosureToggle: vi.fn(),
        onClosureRetry: vi.fn(),
        theme: 'dark',
      }}
    />,
  )

  return { onHazardToggle, onChange }
}

const tab = (name: RegExp) => screen.getByRole('button', { name })
const panels = () => screen.queryAllByRole('region')

/**
 * El nombre accesible tiene que empezar por la etiqueta visible.
 *
 * Es WCAG 2.5.3: quien navega por voz dice «pulsa Leyenda» y el reconocedor
 * compara contra el nombre accesible. Se comprueba acá y no sólo por
 * inspección porque es exactamente la clase de detalle que un `aria-label`
 * «más descriptivo» rompe sin que nada falle.
 */
describe('el nombre accesible contiene la etiqueta visible', () => {
  it.each(['Emergencias', 'Referencia', 'Leyenda'])('%s', (label) => {
    renderControls()
    const name = tab(new RegExp(label, 'i')).getAttribute('aria-label') ?? ''
    expect(name.toLowerCase()).toContain(label.toLowerCase())
  })
})

describe('exclusión: la colisión deja de ser posible', () => {
  it('arranca sin ningún panel abierto', () => {
    renderControls()

    // El mapa es el contenido. Un panel abierto de entrada tapa medio
    // territorio en un teléfono.
    expect(panels()).toHaveLength(0)
  })

  it('nunca hay dos paneles montados a la vez', async () => {
    const user = userEvent.setup()
    renderControls()

    await user.click(tab(/emergencias/i))
    expect(panels()).toHaveLength(1)

    await user.click(tab(/referencia/i))
    expect(panels()).toHaveLength(1)

    await user.click(tab(/leyenda/i))
    expect(panels()).toHaveLength(1)
  })

  it('volver a tocar la ficha abierta devuelve el mapa completo', async () => {
    const user = userEvent.setup()
    renderControls()

    await user.click(tab(/mostrar emergencias/i))
    await user.click(tab(/ocultar emergencias/i))

    // Sin este gesto haría falta buscar una «×» en una esquina para recuperar
    // el mapa, que es justo lo que una interfaz de una mano no puede pedir.
    expect(panels()).toHaveLength(0)
  })
})

describe('cada ficha abre su contenido', () => {
  it('emergencias monta los filtros de capa, con su lista', async () => {
    const user = userEvent.setup()
    renderControls()
    await user.click(tab(/emergencias/i))

    expect(screen.getByRole('checkbox', { name: /incendios/i })).toBeInTheDocument()
  })

  it('referencia monta los interruptores, sin un segundo plegado', async () => {
    const user = userEvent.setup()
    renderControls()
    await user.click(tab(/referencia/i))

    // La ficha ya hace de cabecera: repetir acá el desplegable del riel de
    // escritorio sería un clic de más para llegar a lo mismo.
    expect(screen.getByRole('switch', { name: /amenaza sísmica/i })).toBeInTheDocument()
    // La lluvia ya NO está acá: su interruptor se mudó al widget meteorológico
    // de la barra superior, que en compacto está siempre visible sin abrir
    // ninguna ficha. Ver `WeatherWidget.test.tsx`.
    expect(
      screen.queryByRole('switch', { name: /lluvia pronosticada/i }),
    ).not.toBeInTheDocument()
  })

  it('leyenda monta la escala de confianza', async () => {
    const user = userEvent.setup()
    renderControls()
    await user.click(tab(/leyenda/i))

    expect(screen.getByText(/color: tipo y confianza/i)).toBeInTheDocument()
  })

  it('los controles siguen operando sobre el estado de la aplicación', async () => {
    const user = userEvent.setup()
    const { onHazardToggle } = renderControls()

    await user.click(tab(/referencia/i))
    await user.click(screen.getByRole('switch', { name: /amenaza sísmica/i }))

    expect(onHazardToggle).toHaveBeenCalledTimes(1)
  })
})

describe('la barra resume sin abrir nada', () => {
  it('la ficha de emergencias lleva el total visible', () => {
    renderControls()
    expect(tab(/emergencias/i)).toHaveTextContent('3')
  })

  it('la ficha de referencia cuenta sólo las capas encendidas', () => {
    renderControls({ hazardEnabled: true, closureEnabled: true })
    expect(tab(/referencia/i)).toHaveTextContent('2')
  })

  it('sin capas de referencia encendidas no muestra un cero', () => {
    // Un «0» permanente es ruido: la ausencia ya se lee sola.
    renderControls()
    expect(tab(/referencia/i)).not.toHaveTextContent('0')
  })
})
