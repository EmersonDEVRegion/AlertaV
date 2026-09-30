import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { makeIncident } from '@/test/fixtures'
import { getPlaces, resetPlaces, usePicking } from '@/lib/placesStore'
import { getSheetSnap, resetSheet } from '@/lib/sheetStore'
import { SavedPlaces } from './SavedPlaces'

const CASA = { id: 'a', name: 'Casa', lat: -33.047, lon: -71.442 }

function seed(places: unknown[]) {
  localStorage.setItem('alertav:lugares', JSON.stringify(places))
  resetPlaces()
}

afterEach(() => {
  localStorage.removeItem('alertav:lugares')
  resetPlaces()
  resetSheet()
})

describe('Mis lugares', () => {
  it('sin lugares invita a guardar la casa', async () => {
    render(<SavedPlaces history={[]} onMapCodes={new Set()} onFocusArea={vi.fn()} />)
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
        <SavedPlaces history={[]} onMapCodes={new Set()} onFocusArea={vi.fn()} />
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
    render(<SavedPlaces history={[]} onMapCodes={new Set()} onFocusArea={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Borrar Casa' }))
    expect(getPlaces()).toEqual([])
  })
})
