/**
 * Estado del radar de vehículos.
 *
 * Fija las tres cosas que distinguen a este hook de un `useQuery` a secas:
 * la ventana de 48 h del lado del cliente, la diferencia entre «sin avisos» y
 * «sin datos», y que un 404 no es un error sino una versión del servidor.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import type { VehicleFeedItem, VehicleFeedResponse } from '@/api/vehicleFeedTypes'
import type { HealthStatus } from '@/api/health'
import { VEHICLE_NOW, makeVehicle } from '@/test/fixtures'

const fetchVehicleFeed = vi.fn<() => Promise<VehicleFeedResponse>>()

vi.mock('@/api/vehicleFeed', async () => {
  const actual = await vi.importActual<typeof import('@/api/vehicleFeed')>('@/api/vehicleFeed')
  return { ...actual, fetchVehicleFeed: () => fetchVehicleFeed() }
})

const { useVehicleFeed } = await import('./useVehicleFeed')

function response(items: VehicleFeedItem[], estado: HealthStatus | undefined = 'ok'): VehicleFeedResponse {
  return {
    generado_en: new Date(VEHICLE_NOW).toISOString(),
    horas: 48,
    total: items.length,
    items,
    fuente: {
      collector: 'gbv_vehiculos',
      estado,
      ultima_corrida: new Date(VEHICLE_NOW - 600_000).toISOString(),
      detalle: null,
    },
  }
}

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
}

beforeEach(() => {
  // Sólo `Date`: los temporizadores de react-query siguen siendo reales.
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(VEHICLE_NOW)
  fetchVehicleFeed.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useVehicleFeed', () => {
  it('filtra la ventana de 48 h aunque el servidor mande algo más viejo', async () => {
    fetchVehicleFeed.mockResolvedValue(
      response([
        makeVehicle({ id: 'reciente' }, 1),
        makeVehicle({ id: 'de-ayer' }, 20),
        makeVehicle({ id: 'fuera' }, 49),
      ]),
    )
    const { result } = renderHook(() => useVehicleFeed(true), { wrapper: wrapper() })

    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.items.map((i) => i.id)).toEqual(['reciente', 'de-ayer'])
    expect(result.current.count).toBe(2)
    expect(result.current.recentCount).toBe(1)
  })

  it('cero avisos con la fuente sana es «sin avisos»', async () => {
    fetchVehicleFeed.mockResolvedValue(response([], 'ok'))
    const { result } = renderHook(() => useVehicleFeed(true), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.status).toBe('empty'))
  })

  it('cero avisos con la fuente caída es «sin datos», no calma', async () => {
    fetchVehicleFeed.mockResolvedValue(response([], 'failing'))
    const { result } = renderHook(() => useVehicleFeed(true), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.status).toBe('blind'))
  })

  it('sólo avisos fuera de la ventana y la fuente caída: también «sin datos»', async () => {
    fetchVehicleFeed.mockResolvedValue(response([makeVehicle({}, 60)], 'stale'))
    const { result } = renderHook(() => useVehicleFeed(true), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.status).toBe('blind'))
  })

  it('un 404 es «no disponible», no un error', async () => {
    fetchVehicleFeed.mockRejectedValue(new ApiError('404', 404, '/feed/vehiculos', null))
    const { result } = renderHook(() => useVehicleFeed(true), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.status).toBe('unavailable'))
  })

  it('un corte de red sin nada en caché es «error»', async () => {
    fetchVehicleFeed.mockRejectedValue(new ApiError('red', 0, '/feed/vehiculos', null))
    const { result } = renderHook(() => useVehicleFeed(true), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.status).toBe('error'))
  })

  it('con el interruptor apagado no sale ninguna petición', async () => {
    const { result } = renderHook(() => useVehicleFeed(false), { wrapper: wrapper() })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(fetchVehicleFeed).not.toHaveBeenCalled()
    expect(result.current.status).toBe('loading')
  })
})
