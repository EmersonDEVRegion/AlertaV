/**
 * Ciclo de vida de la capa de amenaza.
 *
 * # Qué fija este archivo
 *
 * Dos cosas, y la primera es la regresión que motivó la reescritura:
 *
 *   1. **`enabled` es intención del usuario y nada más la escribe.** Ninguna
 *      ruta de fallo la toca. El bug reportado —el interruptor que rebotaba a
 *      la izquierda justo cuando la capa terminaba de cargar— salía de que un
 *      cronómetro de 15 s declaraba el fallo a ciegas y `onError` apagaba el
 *      interruptor. Los dos desaparecieron; los tests de abajo impiden que
 *      vuelvan por otra puerta.
 *
 *   2. **La carga diferida sigue intacta.** El archivo no se pide al arrancar,
 *      y apagar y encender no lo vuelve a pedir. Antes se comprobaba mirando
 *      `hasMounted` —porque el montaje del `<Source>` era lo que disparaba la
 *      descarga—; ahora se puede comprobar directamente, contando llamadas al
 *      cliente. Es una prueba más fuerte por el mismo precio.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { HazardGrid } from '@/api/hazardTypes'

const fetchHazardGrid = vi.fn<(signal?: AbortSignal) => Promise<HazardGrid>>()

vi.mock('@/api/hazard', async () => {
  const actual = await vi.importActual<typeof import('@/api/hazard')>('@/api/hazard')
  return { ...actual, fetchHazardGrid: (signal?: AbortSignal) => fetchHazardGrid(signal) }
})

const { useSeismicHazard } = await import('./useSeismicHazard')

/** Rejilla mínima: dos nodos, que es todo lo que miran estos tests. */
function makeGrid(): HazardGrid {
  return {
    cells: { type: 'FeatureCollection', features: [] },
    nodes: {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [-71.5, -33] },
          properties: { value: 0.4 },
        },
        {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [-71.4, -33.1] },
          properties: { value: 0.5 },
        },
      ],
    },
    cellSizeDeg: 0.045,
    metadata: null,
  }
}

/**
 * Cliente propio por test y **sin reintentos**.
 *
 * Con la política real —tres reintentos con retroceso exponencial— un test de
 * fallo tardaría siete segundos en llegar a `status: 'error'`. Lo que se está
 * probando acá es la máquina de estados del hook, no la de react-query.
 */
function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
}

const render = () => renderHook(() => useSeismicHazard(), { wrapper: wrapper() })

beforeEach(() => {
  fetchHazardGrid.mockReset()
  fetchHazardGrid.mockResolvedValue(makeGrid())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('carga diferida', () => {
  it('no pide nada al arrancar', () => {
    const { result } = render()

    expect(result.current.enabled).toBe(false)
    expect(result.current.hasMounted).toBe(false)
    expect(result.current.status).toBe('idle')
    // La prueba directa: el cliente no se llamó. El arranque de la aplicación
    // no paga nada por esta capa.
    expect(fetchHazardGrid).not.toHaveBeenCalled()
  })

  it('pide el archivo en el primer encendido', async () => {
    const { result } = render()

    act(() => result.current.toggle())

    expect(result.current.enabled).toBe(true)
    expect(result.current.hasMounted).toBe(true)
    expect(result.current.status).toBe('loading')

    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(fetchHazardGrid).toHaveBeenCalledTimes(1)
  })

  it('apagar y encender NO vuelve a descargar', async () => {
    const { result } = render()

    act(() => result.current.toggle())
    await waitFor(() => expect(result.current.status).toBe('ready'))

    act(() => result.current.toggle())
    act(() => result.current.toggle())

    expect(result.current.enabled).toBe(true)
    // Una sola descarga en toda la sesión: el resto es `visibility` en el mapa.
    expect(fetchHazardGrid).toHaveBeenCalledTimes(1)
  })

  it('`hasMounted` es monótona: nunca vuelve a false', () => {
    const { result } = render()

    for (let i = 0; i < 6; i += 1) {
      act(() => result.current.toggle())
      expect(result.current.hasMounted).toBe(true)
    }
  })

  it('apagar a mitad de descarga no cancela la descarga', () => {
    const { result } = render()

    act(() => result.current.toggle())
    expect(result.current.status).toBe('loading')

    // El usuario se arrepiente antes de que llegue el archivo.
    act(() => result.current.toggle())

    expect(result.current.enabled).toBe(false)
    // La consulta sigue viva: cancelarla obligaría a empezar de cero al volver
    // a encender, que es peor que terminar una descarga ya pagada a medias.
    expect(result.current.status).toBe('loading')
    expect(fetchHazardGrid).toHaveBeenCalledTimes(1)
  })
})

/**
 * # El bug de la Fase 1
 *
 * Este bloque es la razón de ser del archivo. El síntoma era visual —el pulgar
 * saltando a la izquierda— pero la causa era de contrato: el resultado de una
 * descarga podía escribir sobre la intención del usuario.
 */
describe('la intención del usuario es intocable', () => {
  it('un fallo NO apaga el interruptor', async () => {
    fetchHazardGrid.mockRejectedValue(new Error('la red se cayó'))
    const { result } = render()

    act(() => result.current.toggle())
    await waitFor(() => expect(result.current.status).toBe('error'))

    // Esto es lo que rebotaba. El interruptor se queda donde lo dejó el
    // usuario; lo que cambia es el subtítulo de la fila y el botón de
    // reintentar, que es como se comunica un fallo sin deshacer un gesto.
    expect(result.current.enabled).toBe(true)
    expect(result.current.hasMounted).toBe(true)
  })

  it('expone el motivo del fallo en vez de tragárselo', async () => {
    fetchHazardGrid.mockRejectedValue(new Error('La capa de amenaza no está publicada.'))
    const { result } = render()

    act(() => result.current.toggle())
    await waitFor(() => expect(result.current.status).toBe('error'))

    // «No se pudo cargar» no le dice a nadie si el problema es su red o un
    // artefacto que nunca se generó.
    expect(result.current.errorMessage).toContain('no está publicada')
  })

  it('no hay cronómetro que tumbe una descarga lenta', async () => {
    vi.useFakeTimers()
    try {
      // Una descarga que tarda mucho más que los 15 s del cronómetro viejo.
      let resolve!: (grid: HazardGrid) => void
      fetchHazardGrid.mockReturnValue(
        new Promise<HazardGrid>((r) => {
          resolve = r
        }),
      )

      const { result } = render()
      act(() => result.current.toggle())

      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000)
      })

      // Antes, a los 15 s el cronómetro llamaba a `onError`, `onError` apagaba
      // `enabled`, y la carga terminaba después: el rebote exacto del reporte.
      expect(result.current.enabled).toBe(true)
      expect(result.current.status).toBe('loading')

      await act(async () => {
        resolve(makeGrid())
        await vi.advanceTimersByTimeAsync(0)
      })

      expect(result.current.status).toBe('ready')
      expect(result.current.enabled).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('el estado de carga no depende de eventos del mapa', () => {
    const { result } = render()

    // La API vieja exponía `onLoaded`, `onError` y `attempt` para que el
    // componente de mapa los llamara desde `sourcedata` / `error`. Esa era la
    // carrera: dos relojes —el del mapa y el del hook— que había que
    // sincronizar a mano. Si alguien los reintroduce, el bug vuelve.
    expect(result.current).not.toHaveProperty('onLoaded')
    expect(result.current).not.toHaveProperty('onError')
    expect(result.current).not.toHaveProperty('attempt')
  })
})

describe('reintento', () => {
  it('vuelve a pedir el archivo y deja el interruptor encendido', async () => {
    fetchHazardGrid.mockRejectedValueOnce(new Error('caída puntual'))
    const { result } = render()

    act(() => result.current.toggle())
    await waitFor(() => expect(result.current.status).toBe('error'))

    fetchHazardGrid.mockResolvedValue(makeGrid())
    await act(async () => {
      result.current.retry()
    })

    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(result.current.enabled).toBe(true)
    expect(fetchHazardGrid).toHaveBeenCalledTimes(2)
  })

  it('reintentar desde apagado enciende la capa: es un gesto del usuario', async () => {
    fetchHazardGrid.mockRejectedValueOnce(new Error('caída puntual'))
    const { result } = render()

    act(() => result.current.toggle())
    await waitFor(() => expect(result.current.status).toBe('error'))
    act(() => result.current.toggle())
    expect(result.current.enabled).toBe(false)

    fetchHazardGrid.mockResolvedValue(makeGrid())
    await act(async () => {
      result.current.retry()
    })

    // Pedir un reintento es pedir ver la capa. Dejarla apagada obligaría a un
    // segundo clic, que es justo la fricción que este trabajo vino a quitar.
    expect(result.current.enabled).toBe(true)
  })
})

describe('datos expuestos al mapa', () => {
  it('nunca entrega `undefined`: sin datos es una rejilla vacía', () => {
    const { result } = render()

    expect(result.current.grid.nodes.features).toEqual([])
    expect(result.current.grid.cells.features).toEqual([])
    expect(result.current.count).toBe(0)
  })

  it('cuenta los nodos de la grilla una vez cargada', async () => {
    const { result } = render()

    act(() => result.current.toggle())
    await waitFor(() => expect(result.current.status).toBe('ready'))

    expect(result.current.count).toBe(2)
  })

  it('conserva la identidad de la rejilla entre repintados', async () => {
    const { result, rerender } = render()

    act(() => result.current.toggle())
    await waitFor(() => expect(result.current.status).toBe('ready'))

    const before = result.current.grid
    rerender()

    // `<Source data={…}>` vuelve a subir el GeoJSON al worker cada vez que
    // cambia la identidad del objeto. Una referencia nueva por repintado haría
    // que el mapa retesela miles de celdas sin motivo.
    expect(result.current.grid).toBe(before)
  })
})
