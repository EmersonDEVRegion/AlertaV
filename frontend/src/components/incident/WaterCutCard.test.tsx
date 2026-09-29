import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { makeWaterCut } from '@/test/fixtures'
import { WaterCutCard } from './WaterCutCard'

/** 23-09, 16:00 en Chile: el corte de Viña (11:00 a 17:00) está en curso. */
const DURANTE = Date.parse('2026-09-23T19:00:00Z')
/** 23-09, 18:00 en Chile: ya pasó la hora referencial. */
const DESPUES = Date.parse('2026-09-23T21:00:00Z')

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(DURANTE)
})

afterEach(() => vi.useRealTimers())

describe('WaterCutCard', () => {
  it('muestra lo que Esval publicó y dice que no publica clientes', () => {
    render(<WaterCutCard cut={makeWaterCut()} onClose={vi.fn()} />)

    expect(screen.getByRole('dialog', { name: /corte de agua de esval en viña del mar/i })).toBeInTheDocument()
    expect(screen.getByText(/LOS PENSAMIENTOS/)).toBeInTheDocument()
    expect(screen.getByText('Emergencia')).toBeInTheDocument()
    expect(screen.getByText('Sin suministro alternativo')).toBeInTheDocument()
    expect(screen.getByText('Vida util vencida')).toBeInTheDocument()
    expect(screen.getByText(/Reposición estimada/)).toBeInTheDocument()
    // No hay fila de clientes: Esval no publica ese dato, y un cero mentiría.
    expect(screen.queryByText(/clientes afectados/i)).not.toBeInTheDocument()
    expect(screen.getByText(/no publica cuántos clientes/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /visor de esval/i })).toHaveAttribute(
      'href',
      'https://tupuntodeagua.esval.cl/?sisda=2916567',
    )
  })

  it('avisa cuando la hora referencial ya pasó y el corte sigue publicado', () => {
    vi.setSystemTime(DESPUES)
    render(<WaterCutCard cut={makeWaterCut()} onClose={vi.fn()} />)
    expect(screen.getByText(/ya pasó y el corte sigue publicado/i)).toBeInTheDocument()
  })

  it('cada fila se decide sola: sin motivo, sin fin y sin enlace no hay filas vacías', () => {
    render(
      <WaterCutCard
        cut={makeWaterCut({ motivo: null, fin: null, url_mapa: null, programado: true })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.queryByText('Motivo')).not.toBeInTheDocument()
    expect(screen.queryByText(/Reposición estimada/)).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByText('Programado')).toBeInTheDocument()
  })

  it('sin punto, explica por qué no está en el mapa', () => {
    render(<WaterCutCard cut={makeWaterCut({ coordinates: null })} onClose={vi.fn()} />)
    expect(screen.getByText(/no entregó la ubicación/i)).toBeInTheDocument()
  })

  it('Escape y el botón la cierran', async () => {
    vi.useRealTimers()
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<WaterCutCard cut={makeWaterCut()} onClose={onClose} />)

    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: /cerrar ficha del corte de agua/i }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
