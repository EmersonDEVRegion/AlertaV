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
import type { RoadClosureStatus } from '@/hooks/useRoadClosures'

type Override = Partial<{
  hazardEnabled: boolean
  hazardStatus: HazardStatus
  hazardError: string | null
  closureEnabled: boolean
  closureStatus: RoadClosureStatus
  closureCount: number
  closureCutCount: number
}>

function renderDock(over: Override = {}) {
  const onHazardToggle = vi.fn()
  const onHazardRetry = vi.fn()
  const onClosureToggle = vi.fn()
  const onClosureRetry = vi.fn()

  const props: ReferenceDockProps = {
    hazardEnabled: over.hazardEnabled ?? false,
    hazardStatus: over.hazardStatus ?? 'idle',
    hazardError: over.hazardError ?? null,
    onHazardToggle,
    onHazardRetry,
    closureEnabled: over.closureEnabled ?? false,
    closureStatus: over.closureStatus ?? 'idle',
    closureCount: over.closureCount ?? 0,
    closureCutCount: over.closureCutCount ?? 0,
    onClosureToggle,
    onClosureRetry,
    theme: 'light',
  }

  render(<ReferenceDock {...props} />)
  return {
    onHazardToggle,
    onHazardRetry,
    onClosureToggle,
    onClosureRetry,
  }
}

const hazardSwitch = () => screen.getByRole('switch', { name: /amenaza sísmica/i })
const closureSwitch = () =>
  screen.getByRole('switch', { name: /cortes e intervenciones|cortes de ruta/i })

describe('carga diferida', () => {
  it('las dos arrancan apagadas: nada se pide sin un gesto del usuario', () => {
    renderDock()

    expect(hazardSwitch()).toHaveAttribute('aria-checked', 'false')
    expect(closureSwitch()).toHaveAttribute('aria-checked', 'false')
  })

  it('cada interruptor avisa por separado', async () => {
    const user = userEvent.setup()
    const { onHazardToggle, onClosureToggle } = renderDock()

    await user.click(hazardSwitch())
    expect(onHazardToggle).toHaveBeenCalledTimes(1)
    expect(onClosureToggle).not.toHaveBeenCalled()

    await user.click(closureSwitch())
    expect(onClosureToggle).toHaveBeenCalledTimes(1)
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

/*
 * La cobertura de la LLUVIA se mudó a `WeatherWidget.test.tsx`.
 *
 * Con la tarjeta: su estado vacío («sin lluvia», nunca «sin datos»), la
 * distinción entre seco y caído, y la regla de que la interfaz jamás llame
 * «inundación» a un pronóstico. Son las mismas preguntas y ahora se le hacen al
 * componente que las responde.
 */

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
    expect(closureSwitch()).toBeInTheDocument()
  })

  it('plegado resume cuántas capas quedaron encendidas', async () => {
    const user = userEvent.setup()
    renderDock({ hazardEnabled: true, closureEnabled: true, hazardStatus: 'ready' })

    const header = screen.getByRole('button', { name: /capas de referencia/i })
    await user.click(header)

    expect(header).toHaveTextContent('2')
  })
})
