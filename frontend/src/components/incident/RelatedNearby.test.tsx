import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { IncidentRelacion } from '@/api/types'
import { getSelection, resetSelection } from '@/lib/selectionStore'
import { RelatedNearby, cuando } from './RelatedNearby'

afterEach(() => resetSelection())

const corte: IncidentRelacion = {
  regla: 'choque_corte_luz',
  tipo: 'power_outage',
  nombre: 'Corte de luz',
  code: 'INC-2026-01400',
  distancia_m: 150,
  desfase_min: 12,
}

describe('cuando', () => {
  it('dice antes o después, sin causa', () => {
    expect(cuando(12)).toBe('comenzó 12 min después')
    expect(cuando(-8)).toBe('comenzó 8 min antes')
    expect(cuando(80)).toBe('comenzó 1 h 20 min después')
    expect(cuando(-120)).toBe('comenzó 2 h antes')
    expect(cuando(1)).toBe('casi al mismo tiempo')
  })
})

describe('RelatedNearby', () => {
  it('no dibuja nada sin vínculos (modo sombra)', () => {
    const { container } = render(<RelatedNearby items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('muestra el otro hecho y abre su ficha', () => {
    render(<RelatedNearby items={[corte]} />)
    expect(screen.getByText('Cerca, a la misma hora')).toBeInTheDocument()
    expect(screen.getByText(/a 150 m · comenzó 12 min después/)).toBeInTheDocument()
    expect(screen.getByText(/no sabemos si uno causó el otro/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Corte de luz/ }))
    expect(getSelection()).toEqual({ kind: 'incident', code: 'INC-2026-01400' })
  })

  it('una señal sin ficha (corte de ruta) se muestra sin botón', () => {
    render(
      <RelatedNearby
        items={[{ ...corte, tipo: 'road_closure', nombre: 'Corte de ruta', code: null }]}
      />,
    )
    expect(screen.getByText('Corte de ruta')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })
})
