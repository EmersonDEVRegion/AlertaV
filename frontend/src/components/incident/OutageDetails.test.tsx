import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { OutageDetail } from '@/api/types'
import { OutageDetails } from './OutageDetails'

/** 17-10, 01:17 en Chile: el corte de Rodelillo sigue en curso. */
const AHORA = Date.parse('2026-10-17T04:17:00Z')

const AREA = {
  type: 'MultiPolygon' as const,
  coordinates: [
    [
      [
        [-71.58, -33.06],
        [-71.57, -33.06],
        [-71.57, -33.05],
        [-71.58, -33.06],
      ],
    ],
  ],
}

function corte(over: Partial<OutageDetail> = {}): OutageDetail {
  return {
    provider: 'chilquinta',
    affected_clients: 1912,
    estimated_restoration: '2026-10-17T14:00:41+00:00',
    sector: null,
    outage_count: 1,
    ...over,
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(AHORA)
})

afterEach(() => vi.useRealTimers())

describe('OutageDetails', () => {
  it('un corte con área dice de dónde sale y nombra los barrios de OSM', () => {
    render(<OutageDetails outage={corte({ area: AREA, sectores: ['Rodelillo', 'La Planchada'] })} />)

    expect(screen.getByText('Corte de suministro')).toBeInTheDocument()
    expect(screen.getByText('Reposición estimada')).toBeInTheDocument()
    const sectores = screen.getByRole('list', { name: 'Sectores afectados' })
    expect(sectores).toHaveTextContent('Rodelillo')
    expect(sectores).toHaveTextContent('La Planchada')
    expect(screen.getByText(/OpenStreetMap/)).toBeInTheDocument()
    expect(screen.getByText(/calculada por AlertaV/)).toBeInTheDocument()
  })

  it('sin área ni sectores no inventa nada', () => {
    render(<OutageDetails outage={corte()} />)
    expect(screen.queryByRole('list', { name: 'Sectores afectados' })).not.toBeInTheDocument()
    expect(screen.queryByText(/El ícono marca/)).not.toBeInTheDocument()
  })

  it('un programado se nombra como tal y dice cuándo termina', () => {
    render(<OutageDetails outage={corte({ programado: true, provider: 'cge', area: AREA })} />)
    expect(screen.getByText('Corte programado')).toBeInTheDocument()
    expect(screen.getByText('Termina')).toBeInTheDocument()
    expect(screen.queryByText('Reposición estimada')).not.toBeInTheDocument()
    expect(screen.getByText(/Área informada por CGE/)).toBeInTheDocument()
  })

  it('un área rota no se anuncia', () => {
    render(
      <OutageDetails
        outage={corte({ area: { type: 'MultiPolygon', coordinates: [] } as unknown as typeof AREA })}
      />,
    )
    expect(screen.queryByText(/El ícono marca/)).not.toBeInTheDocument()
  })
})
