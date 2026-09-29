/**
 * Estado de la capa de cortes de agua.
 *
 * Fija lo que la distingue de un `useQuery` a secas: la fila no existe hasta
 * que el backend leyó a Esval alguna vez, un 404 no es un error, y un cero con
 * la fuente caída no es «sin cortes».
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import type { HealthStatus } from '@/api/health'
import type { WaterCut, WaterCutsResponse } from '@/api/waterCutTypes'
import { makeWaterCut } from '@/test/fixtures'

const fetchWaterCuts = vi.fn<() => Promise<WaterCutsResponse>>()

vi.mock('@/api/waterCuts', async () => {
  const actual = await vi.importActual<typeof import('@/api/waterCuts')>('@/api/waterCuts')
  return { ...actual, fetchWaterCuts: () => fetchWaterCuts() }
})

const { useWaterCuts } = await import('./useWaterCuts')

function response(
  cuts: WaterCut[],
  { estado = 'ok', leida = true }: { estado?: HealthStatus; leida?: boolean } = {},
): WaterCutsResponse {
  return {
    generado_en: '2026-09-23T18:31:00+00:00',
    cuts,
    fuente: {
      collector: 'esval_cortes_agua',
      estado,
      ultima_corrida: '2026-09-23T18:30:05+00:00',
      ultima_lectura: leida ? '2026-09-23T18:30:00+00:00' : null,
      detalle: null,
    },
  }
}

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
}

beforeEach(() => { fetchWaterCuts.mockReset() })

describe('useWaterCuts', () => {
  it('con cortes, la fila existe y los de emergencia van primero', async () => {
    const programado = makeWaterCut({ id: 'p', programado: true, inicio: '2026-09-23T18:00:00+00:00' })
    const emergencia = makeWaterCut({ id: 'e', programado: false, inicio: '2026-09-23T14:00:00+00:00' })
    fetchWaterCuts.mockResolvedValue(response([programado, emergencia]))

    const { result } = renderHook(() => useWaterCuts(true), { wrapper: wrapper() })

    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.available).toBe(true)
    expect(result.current.cuts.map((c) => c.id)).toEqual(['e', 'p'])
  })

  it('si el backend nunca leyó a Esval, la fila no aparece (todavía)', async () => {
    fetchWaterCuts.mockResolvedValue(response([], { estado: 'failing', leida: false }))
    const { result } = renderHook(() => useWaterCuts(true), { wrapper: wrapper() })

    await waitFor(() => expect(fetchWaterCuts).toHaveBeenCalled())
    await waitFor(() => expect(result.current.source).not.toBeNull())
    expect(result.current.status).toBe('unavailable')
    expect(result.current.available).toBe(false)
  })

  it('un 404 es «no disponible», no un error', async () => {
    fetchWaterCuts.mockImplementation(() =>
      Promise.reject(new ApiError('404', 404, '/events/water-cuts/geojson', null)),
    )
    const { result } = renderHook(() => useWaterCuts(true), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.status).toBe('unavailable'))
    expect(result.current.available).toBe(false)
  })

  it('cero cortes con la fuente sana es «sin cortes»', async () => {
    fetchWaterCuts.mockResolvedValue(response([]))
    const { result } = renderHook(() => useWaterCuts(true), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.status).toBe('empty'))
    expect(result.current.available).toBe(true)
  })

  it('cero cortes con la fuente caída es «no se sabe», y la fila sigue', async () => {
    fetchWaterCuts.mockResolvedValue(response([], { estado: 'failing' }))
    const { result } = renderHook(() => useWaterCuts(true), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.status).toBe('blind'))
    expect(result.current.available).toBe(true)
  })

  it('un error de red sin nada en caché no muestra la fila', async () => {
    fetchWaterCuts.mockImplementation(() =>
      Promise.reject(new ApiError('red', 0, '/events/water-cuts/geojson', null)),
    )
    const { result } = renderHook(() => useWaterCuts(true), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.available).toBe(false)
  })

  it('apagado por interruptor no consulta nada', async () => {
    const { result } = renderHook(() => useWaterCuts(false), { wrapper: wrapper() })
    expect(result.current.status).toBe('unavailable')
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(fetchWaterCuts).not.toHaveBeenCalled()
  })
})
