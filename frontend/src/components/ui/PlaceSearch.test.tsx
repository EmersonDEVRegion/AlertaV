import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetPlaces } from '@/lib/placesStore'
import { PlaceSearch } from './PlaceSearch'

afterEach(() => {
  localStorage.removeItem('alertav:lugares')
  resetPlaces()
})

describe('buscador de la barra', () => {
  it('encuentra una comuna sin tildes y la elige con Enter', async () => {
    const onPick = vi.fn()
    render(<PlaceSearch onPick={onPick} />)
    const input = screen.getByRole('combobox', { name: 'Buscar comuna o lugar' })

    await userEvent.type(input, 'quilpue')
    expect(screen.getByRole('option', { name: /Quilpué/ })).toBeInTheDocument()
    await userEvent.keyboard('{Enter}')

    expect(onPick).toHaveBeenCalledWith({ kind: 'commune', name: 'Quilpué' })
    expect(input).toHaveValue('')
  })

  it('los lugares guardados van primero', async () => {
    localStorage.setItem(
      'alertav:lugares',
      JSON.stringify([{ id: 'a', name: 'Casa', lat: -33.047, lon: -71.442 }]),
    )
    resetPlaces()
    const onPick = vi.fn()
    render(<PlaceSearch onPick={onPick} />)
    await userEvent.click(screen.getByRole('combobox', { name: 'Buscar comuna o lugar' }))

    const [first] = screen.getAllByRole('option')
    expect(first).toHaveTextContent('Casa')
    await userEvent.click(first!)
    expect(onPick).toHaveBeenCalledWith({
      kind: 'place',
      id: 'a',
      name: 'Casa',
      lat: -33.047,
      lon: -71.442,
    })
  })
})
