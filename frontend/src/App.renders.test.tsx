/**
 * Regresión de renders: la aplicación quieta no puede repintar el mapa.
 *
 * Antes, un reloj de 1 s vivía en `App` y repintaba el mapa, los dos paneles y
 * la barra 60 veces por minuto sin que cambiara un solo dato. Esta prueba deja
 * la app un minuto quieta —la API respondiendo siempre lo mismo— y cuenta.
 *
 * Los componentes pesados se sustituyen por contadores envueltos en `memo`,
 * igual que los reales: lo que se mide es si `App` les cambia la identidad de
 * alguna prop sin motivo, que es exactamente lo que anula un `memo`.
 */
import { act, render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { memo } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const renders = { map: 0, panel: 0, header: 0 }

vi.mock('@/components/map/IncidentMap', () => ({
  IncidentMap: memo(function IncidentMap() {
    renders.map += 1
    return null
  }),
}))

vi.mock('@/components/ui/SidePanel', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/components/ui/SidePanel')>()),
  SidePanel: memo(function SidePanel() {
    renders.panel += 1
    return null
  }),
}))

vi.mock('@/components/ui/AppHeader', () => ({
  AppHeader: memo(function AppHeader() {
    renders.header += 1
    return null
  }),
}))

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** La API de siempre: nada cambia entre un sondeo y el siguiente. */
function quietApi(input: RequestInfo | URL): Promise<Response> {
  const url = String(input)
  if (url.includes('/incidents/active')) return Promise.resolve(json([]))
  if (url.includes('/collectors/health')) {
    return Promise.resolve(json({ by_family: {}, collectors: [] }))
  }
  if (url.includes('/events/seismic')) return Promise.resolve(json([]))
  return Promise.resolve(json({ detail: 'no existe' }, 404))
}

describe('App quieta', () => {
  beforeEach(() => {
    vi.useFakeTimers({
      toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'],
    })
    // Escritorio: el panel lateral se monta (en teléfono va la barra de fichas).
    vi.stubGlobal(
      'matchMedia',
      (query: string) =>
        ({
          matches: false,
          media: query,
          onchange: null,
          addEventListener() {},
          removeEventListener() {},
          addListener() {},
          removeListener() {},
          dispatchEvent: () => false,
        }) as unknown as MediaQueryList,
    )
    vi.stubGlobal('fetch', vi.fn(quietApi))
    renders.map = 0
    renders.panel = 0
    renders.header = 0
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('no repinta el mapa, el panel ni la barra durante un minuto sin datos nuevos', async () => {
    const { default: App } = await import('@/App')
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    )
    // Arranque: primeras respuestas y efectos iniciales.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })
    const base = { ...renders }

    // Un segundo por `act`: si React agrupara todo el minuto en un solo lote,
    // un tic de 1 s pasaría inadvertido.
    for (let second = 0; second < 60; second += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000)
      })
    }

    expect(renders.map - base.map).toBe(0)
    expect(renders.panel - base.panel).toBe(0)
    expect(renders.header - base.header).toBe(0)
    client.clear()
  })
})
