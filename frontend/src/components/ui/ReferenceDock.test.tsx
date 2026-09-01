/**
 * Controlador de capas de referencia.
 *
 * Hereda la cobertura que vivía en `SidePanel.test.tsx` —la capa se mudó, los
 * tests con ella— y añade lo que el panel viejo no podía comprobar porque no
 * tenía sitio para mostrarlo: el motivo real de un fallo y la nota de relevo
 * por zoom.
 *
 * El test que más importa sigue siendo el del estado vacío. Una comuna ausente
 * del GeoJSON significa "comuna seca", así que una colección vacía es una
 * respuesta CORRECTA y, en verano, la respuesta normal durante semanas. Si
 * alguien tratara ese caso como un fallo, la app pasaría media temporada
 * diciendo "sin datos" sobre un backend que responde perfecto.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ReferenceDock } from './ReferenceDock'
import type { ReferenceDockProps } from './ReferenceDock'
import type { HazardStatus } from '@/hooks/useSeismicHazard'
import type { RainStatus } from '@/hooks/useRainLayer'

type Override = Partial<{
  hazardEnabled: boolean
  hazardStatus: HazardStatus
  hazardError: string | null
  rainEnabled: boolean
  rainStatus: RainStatus
  rainCount: number
  rainRiskCount: number
}>

function renderDock(over: Override = {}) {
  const onHazardToggle = vi.fn()
  const onHazardRetry = vi.fn()
  const onRainToggle = vi.fn()
  const onRainRetry = vi.fn()

  const props: ReferenceDockProps = {
    hazardEnabled: over.hazardEnabled ?? false,
    hazardStatus: over.hazardStatus ?? 'idle',
    hazardError: over.hazardError ?? null,
    onHazardToggle,
    onHazardRetry,
    rainEnabled: over.rainEnabled ?? false,
    rainStatus: over.rainStatus ?? 'idle',
    rainCount: over.rainCount ?? 0,
    rainRiskCount: over.rainRiskCount ?? 0,
    onRainToggle,
    onRainRetry,
    theme: 'light',
  }

  render(<ReferenceDock {...props} />)
  return { onHazardToggle, onHazardRetry, onRainToggle, onRainRetry }
}

const hazardSwitch = () => screen.getByRole('switch', { name: /amenaza sísmica/i })
const rainSwitch = () => screen.getByRole('switch', { name: /lluvia pronosticada/i })

describe('carga diferida', () => {
  it('las dos arrancan apagadas: nada se pide sin un gesto del usuario', () => {
    renderDock()

    expect(hazardSwitch()).toHaveAttribute('aria-checked', 'false')
    expect(rainSwitch()).toHaveAttribute('aria-checked', 'false')
  })

  it('cada interruptor avisa por separado', async () => {
    const user = userEvent.setup()
    const { onHazardToggle, onRainToggle } = renderDock()

    await user.click(hazardSwitch())
    expect(onHazardToggle).toHaveBeenCalledTimes(1)
    expect(onRainToggle).not.toHaveBeenCalled()

    await user.click(rainSwitch())
    expect(onRainToggle).toHaveBeenCalledTimes(1)
  })
})

describe('amenaza sísmica', () => {
  it('describe la espera sin prometer un resultado', () => {
    renderDock({ hazardEnabled: true, hazardStatus: 'loading' })
    expect(screen.getByText(/descargando modelo/i)).toBeInTheDocument()
  })

  it('muestra el MOTIVO del fallo, no un «no se pudo cargar» genérico', () => {
    renderDock({
      hazardEnabled: true,
      hazardStatus: 'error',
      hazardError: 'La capa de amenaza no está publicada en el servidor.',
    })

    // La diferencia es accionable: «no está publicada» le dice a quien despliega
    // que falta correr el script; «no se pudo cargar» le hace revisar su wifi.
    expect(screen.getByText(/no está publicada en el servidor/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reintentar/i })).toBeInTheDocument()
  })

  it('el interruptor sigue encendido tras un fallo', () => {
    renderDock({ hazardEnabled: true, hazardStatus: 'error', hazardError: 'caída' })

    // La contraparte visual del arreglo de la Fase 1: el estado del control lo
    // decide el usuario, y el resultado de la descarga se cuenta en el
    // subtítulo. Un control que se apaga solo deja de ser un control.
    expect(hazardSwitch()).toHaveAttribute('aria-checked', 'true')
  })

  it('no llama evento a un modelo probabilístico', () => {
    renderDock({ hazardEnabled: true, hazardStatus: 'ready' })

    expect(screen.getByText(/no un evento en curso/i)).toBeInTheDocument()
  })

  it('declara la resolución del modelo en vez de dejarla implícita', () => {
    renderDock({ hazardEnabled: true, hazardStatus: 'ready' })

    /*
     * Antes acá se anunciaba un relevo de representación —«concentración
     * regional → celdas del modelo»— porque la capa cambiaba de forma al hacer
     * zoom. Ya no: es una sola superficie continua en todo el rango.
     *
     * Lo que queda por decir es la RESOLUCIÓN, y no es un detalle cosmético.
     * Sin ella, un degradado suave se lee como una medición continua del
     * terreno, cuando en realidad es la interpolación de una grilla de 5 km:
     * dentro de una celda, el modelo no distingue un cerro de una quebrada.
     */
    expect(screen.getByText(/celdas del modelo, ~5 km/i)).toBeInTheDocument()
    expect(screen.getByText(/retícula aparece al acercarse/i)).toBeInTheDocument()
  })
})

describe('lluvia pronosticada', () => {
  it('el estado vacío dice "sin lluvia", nunca "sin datos"', () => {
    renderDock({ rainEnabled: true, rainStatus: 'empty' })

    expect(screen.getByText(/sin lluvia pronosticada/i)).toBeInTheDocument()
    expect(screen.queryByText(/sin datos/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/no se pudo cargar/i)).not.toBeInTheDocument()
    // Y no ofrece reintentar: no hay nada que reintentar.
    expect(screen.queryByRole('button', { name: /reintentar/i })).not.toBeInTheDocument()
  })

  it('distingue el error del estado seco', () => {
    renderDock({ rainEnabled: true, rainStatus: 'error' })

    expect(screen.getByText(/no se pudo cargar/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reintentar/i })).toBeInTheDocument()
  })

  it('cuenta las comunas en riesgo cuando las hay', () => {
    renderDock({ rainEnabled: true, rainStatus: 'ready', rainCount: 5, rainRiskCount: 2 })
    expect(screen.getByText(/5 comunas · 2 con riesgo/i)).toBeInTheDocument()
  })

  it('nunca llama "inundación" a un pronóstico', () => {
    renderDock({ rainEnabled: true, rainStatus: 'ready', rainCount: 3, rainRiskCount: 1 })

    // El hand-off del backend es explícito: `riesgo_inundacion: true` es un
    // umbral cruzado por un modelo, no una inundación, y tampoco una alerta
    // oficial — esas las declara SENAPRED y llegan por otra vía.
    expect(screen.getByText(/SENAPRED/)).toBeInTheDocument()
    expect(screen.getByTitle(/riesgo de inundación pronosticado/i)).toBeInTheDocument()
    // Nada en el panel afirma que HAY una inundación.
    expect(screen.queryByText(/^\s*inundaci[óo]n\s*$/i)).not.toBeInTheDocument()
  })
})

describe('plegado del dock', () => {
  it('arranca abierto: esconder el control lo volvería invisible', () => {
    renderDock()
    expect(
      screen.getByRole('button', { name: /capas de referencia/i }),
    ).toHaveAttribute('aria-expanded', 'true')
  })

  it('al plegarse deja los interruptores en el DOM, no los desmonta', async () => {
    const user = userEvent.setup()
    renderDock()

    await user.click(screen.getByRole('button', { name: /capas de referencia/i }))

    // Se anima con `grid-template-rows`, así que el contenido sigue existiendo.
    // Si alguien lo cambiara por un `&&`, la transición se perdería y el panel
    // saltaría.
    expect(hazardSwitch()).toBeInTheDocument()
    expect(rainSwitch()).toBeInTheDocument()
  })

  it('plegado resume cuántas capas quedaron encendidas', async () => {
    const user = userEvent.setup()
    renderDock({ hazardEnabled: true, rainEnabled: true, hazardStatus: 'ready' })

    const header = screen.getByRole('button', { name: /capas de referencia/i })
    await user.click(header)

    expect(header).toHaveTextContent('2')
  })
})
