import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactElement } from 'react'
import { render as renderBase, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { makeIncident } from '@/test/fixtures'
import { getPlaces, resetPlaces, usePicking } from '@/lib/placesStore'
import { getSheetSnap, resetSheet } from '@/lib/sheetStore'
import { SavedPlaces } from './SavedPlaces'

const CASA = { id: 'a', name: 'Casa', lat: -33.047, lon: -71.442 }

/** Dos cuarteles de Quilpué, con la forma que sirve `/events/cuarteles`. */
const CUARTELES = {
  type: 'FeatureCollection',
  metadata: { fuente: 'SIG Bomberos de Chile (sig.bomberos.cl)', generado: '2026-09-30T14:46:23+00:00' },
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [-71.437316, -33.064228] },
      properties: { tipo: 'compania', nombre: 'Quinta de Quilpué', cuerpo: 'Quilpué', numero: 5, direccion: 'Colina de Oro 2155', comuna: 'Quilpué', telefono: '322910310' },
    },
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [-71.442652, -33.049927] },
      properties: { tipo: 'compania', nombre: 'Segunda de Quilpué', cuerpo: 'Quilpué', numero: 2, direccion: 'Esmeralda 700', comuna: 'Quilpué', telefono: null },
    },
  ],
}

function render(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return renderBase(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(CUARTELES), { status: 200 })),
  )
})

function seed(places: unknown[]) {
  localStorage.setItem('alertav:lugares', JSON.stringify(places))
  resetPlaces()
}

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.removeItem('alertav:lugares')
  resetPlaces()
  resetSheet()
})

describe('Mis lugares', () => {
  it('sin lugares invita a guardar la casa', async () => {
    render(<SavedPlaces history={[]} onMapCodes={new Set()} onFocusArea={vi.fn()} onFocusCuartel={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /Guarda tu casa/ }))
    expect(screen.getByRole('radio', { name: 'Casa' })).toBeChecked()
  })

  it('cuenta lo que está en curso a menos de 5 km, no lo que ya salió del mapa', async () => {
    seed([CASA])
    const near = makeIncident({ code: 'INC-2026-00001', lat: -33.057, lon: -71.442 })
    const gone = makeIncident({ code: 'INC-2026-00002', lat: -33.05, lon: -71.44 })
    const far = makeIncident({ code: 'INC-2026-00003', lat: -33.047, lon: -71.6 })
    const onFocusArea = vi.fn()
    render(
      <SavedPlaces
        history={[near, gone, far]}
        onMapCodes={new Set([near.code, far.code])}
        onFocusArea={onFocusArea}
        onFocusCuartel={vi.fn()}
      />,
    )

    expect(screen.getByText('1 emergencia en curso a menos de 5 km')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /^Casa/ }))
    expect(onFocusArea).toHaveBeenCalledWith({ kind: 'place', ...CASA })
  })

  it('«Elegir en el mapa» baja la hoja y entra en modo mira con el nombre', async () => {
    seed([CASA])
    function Probe() {
      return <span data-testid="picking">{usePicking() ?? ''}</span>
    }
    render(
      <>
        <SavedPlaces history={[]} onMapCodes={new Set()} onFocusArea={vi.fn()} onFocusCuartel={vi.fn()} />
        <Probe />
      </>,
    )
    await userEvent.click(screen.getByRole('button', { name: '+ Agregar' }))
    // Casa ya está: la primera opción libre es Trabajo.
    expect(screen.getByRole('radio', { name: 'Trabajo' })).toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Elegir en el mapa' }))
    expect(screen.getByTestId('picking')).toHaveTextContent('Trabajo')
    expect(getSheetSnap()).toBe('peek')
  })

  it('borrar', async () => {
    seed([CASA])
    render(<SavedPlaces history={[]} onMapCodes={new Set()} onFocusArea={vi.fn()} onFocusCuartel={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Borrar Casa' }))
    expect(getPlaces()).toEqual([])
  })

  it('muestra el cuartel más cercano, en línea recta, y vuela a él', async () => {
    seed([CASA])
    const onFocusCuartel = vi.fn()
    render(
      <SavedPlaces
        history={[]}
        onMapCodes={new Set()}
        onFocusArea={vi.fn()}
        onFocusCuartel={onFocusCuartel}
      />,
    )
    const linea = await screen.findByRole('button', { name: /Cuartel más cercano: Segunda de Quilpué/ })
    await userEvent.click(linea)
    expect(screen.getByText(/Distancia en línea recta/)).toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('button', { name: 'Ver en el mapa' })[0]!)
    expect(onFocusCuartel).toHaveBeenCalledWith(-71.442652, -33.049927)
    // El teléfono del cuartel no se muestra: en una emergencia se llama al 132.
    expect(screen.queryByText(/322910310/)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '132' })).toHaveAttribute('href', 'tel:132')
  })
})
