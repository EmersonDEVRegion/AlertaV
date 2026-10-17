/**
 * Capas de referencia: los azulejos y la ficha que aparece al encender uno.
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
import { ReferenceLayers } from './ReferenceLayers'
import type { ReferenceLayersProps } from './ReferenceLayers'
import type { CuartelesStatus } from '@/hooks/useCuarteles'
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
  cuartelesEnabled: boolean
  cuartelesStatus: CuartelesStatus
  cuartelesCount: number
}>

function renderLayers(over: Override = {}) {
  const onHazardToggle = vi.fn()
  const onHazardRetry = vi.fn()
  const onClosureToggle = vi.fn()
  const onClosureRetry = vi.fn()
  const onCuartelesToggle = vi.fn()

  const props: ReferenceLayersProps = {
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
    cuartelesEnabled: over.cuartelesEnabled ?? false,
    cuartelesStatus: over.cuartelesStatus ?? 'idle',
    cuartelesCount: over.cuartelesCount ?? 0,
    cuartelesError: null,
    onCuartelesToggle,
    onCuartelesRetry: vi.fn(),
    theme: 'light',
  }

  render(<ReferenceLayers {...props} />)
  return {
    onHazardToggle,
    onHazardRetry,
    onClosureToggle,
    onClosureRetry,
    onCuartelesToggle,
  }
}

const hazardSwitch = () => screen.getByRole('button', { name: /amenaza sísmica/i })
const closureSwitch = () => screen.getByRole('button', { name: /cortes de ruta/i })

describe('carga diferida', () => {
  it('las dos arrancan apagadas: nada se pide sin un gesto del usuario', () => {
    renderLayers()

    expect(hazardSwitch()).toHaveAttribute('aria-pressed', 'false')
    expect(closureSwitch()).toHaveAttribute('aria-pressed', 'false')
  })

  it('cada azulejo avisa por separado', async () => {
    const user = userEvent.setup()
    const { onHazardToggle, onClosureToggle } = renderLayers()

    await user.click(hazardSwitch())
    expect(onHazardToggle).toHaveBeenCalledTimes(1)
    expect(onClosureToggle).not.toHaveBeenCalled()

    await user.click(closureSwitch())
    expect(onClosureToggle).toHaveBeenCalledTimes(1)
  })
})

describe('amenaza sísmica', () => {
  it('describe la espera sin prometer un resultado', () => {
    renderLayers({ hazardEnabled: true, hazardStatus: 'loading' })
    expect(screen.getByText(/descargando modelo/i)).toBeInTheDocument()
  })

  it('muestra el MOTIVO del fallo, no un «no se pudo cargar» genérico', () => {
    renderLayers({
      hazardEnabled: true,
      hazardStatus: 'error',
      hazardError: 'La capa de amenaza no está publicada en el servidor.',
    })

    // La diferencia es accionable: «no está publicada» le dice a quien despliega
    // que falta correr el script; «no se pudo cargar» le hace revisar su wifi.
    expect(screen.getByText(/no está publicada en el servidor/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reintentar/i })).toBeInTheDocument()
  })

  it('el azulejo sigue encendido tras un fallo', () => {
    renderLayers({ hazardEnabled: true, hazardStatus: 'error', hazardError: 'caída' })

    // La contraparte visual del arreglo de la Fase 1: el estado del control lo
    // decide el usuario, y el resultado de la descarga se cuenta en el
    // subtítulo. Un control que se apaga solo deja de ser un control.
    expect(hazardSwitch()).toHaveAttribute('aria-pressed', 'true')
  })

  it('no llama evento a un modelo probabilístico', () => {
    renderLayers({ hazardEnabled: true, hazardStatus: 'ready' })

    expect(screen.getByText(/no un evento en curso/i)).toBeInTheDocument()
  })

  it('declara la resolución del modelo en vez de dejarla implícita', () => {
    renderLayers({ hazardEnabled: true, hazardStatus: 'ready' })

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
    expect(screen.getByText(/cada ~5 km, interpolado/i)).toBeInTheDocument()
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

describe('ficha de la capa encendida', () => {
  it('apagadas no hay fichas: el mosaico es todo', () => {
    renderLayers()
    expect(screen.queryAllByRole('region')).toHaveLength(1) // sólo el grupo «Referencia»
  })

  it('cortes de ruta: el azulejo dice lo accionable y la ficha el detalle', () => {
    renderLayers({ closureEnabled: true, closureStatus: 'ready', closureCount: 14, closureCutCount: 2 })
    expect(closureSwitch()).toHaveTextContent('2 cortadas')
    expect(screen.getByText('2 rutas cortadas · 14 vigentes')).toBeInTheDocument()
  })

  it('sin cortes informados no es un fallo', () => {
    renderLayers({ closureEnabled: true, closureStatus: 'empty' })
    expect(screen.getByText(/la capa está encendida y al día/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /reintentar/i })).not.toBeInTheDocument()
  })
})

describe('Cuarteles de Bomberos', () => {
  it('el azulejo avisa al tocarlo', async () => {
    const { onCuartelesToggle } = renderLayers({ cuartelesStatus: 'ready', cuartelesCount: 146 })
    await userEvent.click(screen.getByRole('button', { name: /cuarteles de bomberos/i }))
    expect(onCuartelesToggle).toHaveBeenCalledOnce()
  })

  it('encendida, cuenta los cuarteles y recuerda que se llama al 132', () => {
    renderLayers({ cuartelesEnabled: true, cuartelesStatus: 'ready', cuartelesCount: 146 })
    expect(screen.getByText('146 cuarteles en la región')).toBeInTheDocument()
    expect(screen.getByText(/llama al 132/)).toBeInTheDocument()
  })
})


describe('avisos de la DMC', () => {
  it('sin las props de la DMC no hay cuarto azulejo', () => {
    renderLayers()
    expect(screen.queryByRole('button', { name: /avisos dmc/i })).not.toBeInTheDocument()
  })

  it('con ellas aparece apagado y encenderlo llama al toggle', async () => {
    const onMeteoToggle = vi.fn()
    render(
      <ReferenceLayers
        hazardEnabled={false}
        hazardStatus="idle"
        hazardError={null}
        onHazardToggle={vi.fn()}
        onHazardRetry={vi.fn()}
        closureEnabled={false}
        closureStatus="idle"
        closureCount={0}
        closureCutCount={0}
        onClosureToggle={vi.fn()}
        onClosureRetry={vi.fn()}
        cuartelesEnabled={false}
        cuartelesStatus="idle"
        cuartelesCount={0}
        cuartelesError={null}
        onCuartelesToggle={vi.fn()}
        onCuartelesRetry={vi.fn()}
        meteoEnabled={false}
        meteoStatus="idle"
        meteoZoneCount={0}
        onMeteoToggle={onMeteoToggle}
        theme="light"
      />,
    )
    const tile = screen.getByRole('button', { name: /avisos dmc/i })
    expect(tile).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(tile)
    expect(onMeteoToggle).toHaveBeenCalledOnce()
  })

  it('encendida y sin avisos lo dice como calma, no como falla', () => {
    render(
      <ReferenceLayers
        hazardEnabled={false}
        hazardStatus="idle"
        hazardError={null}
        onHazardToggle={vi.fn()}
        onHazardRetry={vi.fn()}
        closureEnabled={false}
        closureStatus="idle"
        closureCount={0}
        closureCutCount={0}
        onClosureToggle={vi.fn()}
        onClosureRetry={vi.fn()}
        cuartelesEnabled={false}
        cuartelesStatus="idle"
        cuartelesCount={0}
        cuartelesError={null}
        onCuartelesToggle={vi.fn()}
        onCuartelesRetry={vi.fn()}
        meteoEnabled
        meteoStatus="empty"
        meteoZoneCount={0}
        onMeteoToggle={vi.fn()}
        theme="dark"
      />,
    )
    expect(screen.getByText('Sin avisos vigentes para la región')).toBeInTheDocument()
    expect(screen.getByText(/no una alerta de SENAPRED/)).toBeInTheDocument()
  })
})
