import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { VehicleFeedItem } from '@/api/vehicleFeedTypes'
import type { VehicleFeedState } from '@/hooks/useVehicleFeed'
import { VEHICLE_NOW, makeVehicle } from '@/test/fixtures'
import { VehicleCard } from './VehicleCard'
import { VehicleRadarButton, badgeFor } from './VehicleRadarButton'
import { VehicleRadarPanel } from './VehicleRadarPanel'

function feed(over: Partial<VehicleFeedState> = {}): VehicleFeedState {
  const items: VehicleFeedItem[] = over.items ?? []
  return {
    status: items.length > 0 ? 'ready' : 'empty',
    items,
    count: items.length,
    recentCount: 0,
    source: {
      collector: 'gbv_vehiculos',
      estado: 'ok',
      ultima_corrida: new Date(VEHICLE_NOW - 12 * 60_000).toISOString(),
      detalle: null,
    },
    sourceStatus: 'ok',
    now: VEHICLE_NOW,
    refreshFailed: false,
    truncated: false,
    isFetching: false,
    refetch: vi.fn(),
    ...over,
  }
}

describe('VehicleCard', () => {
  it('la patente va formateada y se lee de corrido para el lector de pantalla', () => {
    render(
      <ul>
        <VehicleCard item={makeVehicle()} now={VEHICLE_NOW} open={false} onToggle={vi.fn()} />
      </ul>,
    )
    expect(screen.getByText('LK·XV·55')).toBeInTheDocument()
    expect(screen.getByText('Patente L K X V 5 5')).toBeInTheDocument()
    expect(screen.getByText('Robado')).toBeInTheDocument()
    expect(screen.getByText('robo 22 sep')).toBeInTheDocument()
  })

  it('dice «visto hace», porque el tiempo es de AlertaV y no del robo', () => {
    render(
      <ul>
        <VehicleCard item={makeVehicle({}, 3)} now={VEHICLE_NOW} open={false} onToggle={vi.fn()} />
      </ul>,
    )
    expect(screen.getByText('visto hace 3 h')).toBeInTheDocument()
  })

  it('sin patente dibuja el hueco y no inventa nada', () => {
    const item = makeVehicle({
      estado: 'abandonado',
      patente: null,
      fecha_delito: null,
      tiempo_abandono: '4 días',
    })
    render(
      <ul>
        <VehicleCard item={item} now={VEHICLE_NOW} open={false} onToggle={vi.fn()} />
      </ul>,
    )
    expect(screen.getByText('S/PATENTE')).toBeInTheDocument()
    expect(screen.getByText('hace 4 días')).toBeInTheDocument()
  })

  it('un aviso de menos de 6 h se anuncia como nuevo; uno de 7 h no', () => {
    const { rerender } = render(
      <ul>
        <VehicleCard item={makeVehicle({}, 2)} now={VEHICLE_NOW} open={false} onToggle={vi.fn()} />
      </ul>,
    )
    expect(screen.getByText(/Nuevo/)).toBeInTheDocument()
    rerender(
      <ul>
        <VehicleCard item={makeVehicle({}, 7)} now={VEHICLE_NOW} open={false} onToggle={vi.fn()} />
      </ul>,
    )
    expect(screen.queryByText(/Nuevo/)).not.toBeInTheDocument()
  })

  it('abierta muestra el detalle y el enlace a GBV en otra pestaña', () => {
    render(
      <ul>
        <VehicleCard item={makeVehicle()} now={VEHICLE_NOW} open onToggle={vi.fn()} />
      </ul>,
    )
    expect(screen.getByText('Robo desde vía pública')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /Ver el aviso en GBV/ })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })

  it('un lugar sin ubicar lo dice, y no lo presenta como comuna', () => {
    const item = makeVehicle({ comuna: null, region: 'sin_ubicar', lugar: 'los araucanos 290' })
    render(
      <ul>
        <VehicleCard item={item} now={VEHICLE_NOW} open onToggle={vi.fn()} />
      </ul>,
    )
    expect(screen.getByText('Sin ubicar')).toBeInTheDocument()
    expect(screen.getByText(/no alcanza para saber la comuna/)).toBeInTheDocument()
  })
})

describe('VehicleRadarPanel', () => {
  it('sin avisos y con la fuente sana: «Sin avisos recientes»', () => {
    render(<VehicleRadarPanel feed={feed()} onClose={vi.fn()} />)
    expect(screen.getByText('Sin avisos recientes')).toBeInTheDocument()
    expect(screen.queryByText(/Sin datos de GBV/)).not.toBeInTheDocument()
  })

  it('sin avisos y con la fuente caída: no afirma calma', () => {
    render(
      <VehicleRadarPanel
        feed={feed({ status: 'blind', sourceStatus: 'failing' })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText(/Sin datos de GBV/)).toBeInTheDocument()
    expect(screen.getByText(/no significa que no haya robos/)).toBeInTheDocument()
    expect(screen.queryByText('Sin avisos recientes')).not.toBeInTheDocument()
  })

  it('con error ofrece reintentar', async () => {
    const refetch = vi.fn()
    render(<VehicleRadarPanel feed={feed({ status: 'error', refetch })} onClose={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Reintentar' }))
    expect(refetch).toHaveBeenCalledOnce()
  })

  it('los filtros por estado filtran sin volver a la red', async () => {
    const items = [
      makeVehicle({ id: 'r', patente: 'LKXV55' }, 1),
      makeVehicle({ id: 'c', estado: 'recuperado', patente: 'YW8869' }, 2),
      makeVehicle({ id: 'a', estado: 'abandonado', patente: null, marca: 'Kia' }, 3),
    ]
    const state = feed({ items })
    render(<VehicleRadarPanel feed={state} onClose={vi.fn()} />)

    const list = screen.getByRole('list', { name: 'Avisos de vehículos' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)

    await userEvent.click(screen.getByRole('button', { name: /Recuperados/ }))
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)
    expect(within(list).getByText('YW·88·69')).toBeInTheDocument()
    expect(state.refetch).not.toHaveBeenCalled()
  })

  it('tocar una tarjeta la despliega; tocarla de nuevo la cierra', async () => {
    render(<VehicleRadarPanel feed={feed({ items: [makeVehicle()] })} onClose={vi.fn()} />)
    const card = screen.getByRole('button', { name: /Toyota Rav4/ })
    await userEvent.click(card)
    expect(card).toHaveAttribute('aria-expanded', 'true')
    await userEvent.click(card)
    expect(card).toHaveAttribute('aria-expanded', 'false')
  })

  it('Escape cierra', async () => {
    const onClose = vi.fn()
    render(<VehicleRadarPanel feed={feed()} onClose={onClose} />)
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('cita la fuente y la edad de la última lectura', () => {
    render(<VehicleRadarPanel feed={feed()} onClose={vi.fn()} />)
    expect(screen.getByRole('link', { name: 'GBV' })).toHaveAttribute('href', 'https://gbvspa.cl')
    expect(screen.getByText(/última lectura hace 12 minutos/)).toBeInTheDocument()
  })
})

describe('VehicleRadarButton', () => {
  it('el contador: número con avisos, «–» sin datos, nada con cero sano o sin saber', () => {
    expect(badgeFor({ status: 'ready', count: 4 })).toBe('4')
    expect(badgeFor({ status: 'ready', count: 140 })).toBe('99+')
    expect(badgeFor({ status: 'blind', count: 0 })).toBe('–')
    expect(badgeFor({ status: 'empty', count: 0 })).toBeNull()
    expect(badgeFor({ status: 'loading', count: 0 })).toBeNull()
    expect(badgeFor({ status: 'error', count: 0 })).toBeNull()
  })

  it('el nombre accesible dice cuántos hay y cuántos son recientes', () => {
    const items = [makeVehicle({ id: 'a' }), makeVehicle({ id: 'b' })]
    render(
      <VehicleRadarButton
        feed={feed({ items, recentCount: 1 })}
        open={false}
        onToggle={vi.fn()}
      />,
    )
    expect(
      screen.getByRole('button', {
        name: 'Radar de vehículos: 2 avisos en las últimas 48 h, 1 de las últimas 6 h',
      }),
    ).toHaveAttribute('aria-expanded', 'false')
  })
})
