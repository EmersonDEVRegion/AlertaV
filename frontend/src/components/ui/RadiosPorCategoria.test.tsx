import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PushNotificationsState } from '@/hooks/usePushNotifications'
import { RadiosPorCategoria } from './RadiosPorCategoria'

function state(radios: Record<string, number> = {}): PushNotificationsState {
  return {
    phase: 'on',
    step: null,
    message: null,
    server: {
      enabled: true,
      public_key: 'BP4z',
      reason: null,
      incident_radius_m: 5000,
      radios_por_defecto: { fire: 5000, traffic: 2000, transito: 0 },
      categorias: [
        { clave: 'fire', etiqueta: 'Incendios' },
        { clave: 'transito', etiqueta: 'Tránsito' },
      ],
      incident_min_confidence: 0.3,
      incident_min_sources: 2,
      seismic_min_magnitude: 3.5,
    },
    preferences: { notifyIncidents: true, notifySeismic: true, radios },
    locatedAt: null,
    probeResult: null,
    enable: vi.fn(async () => undefined),
    disable: vi.fn(async () => undefined),
    setPreferences: vi.fn(async () => undefined),
    refreshLocation: vi.fn(async () => undefined),
    sendProbe: vi.fn(async () => undefined),
  }
}

afterEach(() => vi.useRealTimers())

describe('RadiosPorCategoria', () => {
  it('el tránsito viene apagado y «Activar» lo deja en 2 km', async () => {
    vi.useFakeTimers()
    const push = state()
    render(<RadiosPorCategoria push={push} />)

    expect(screen.getByText('5 km')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Activar avisos de Tránsito: 2 km' }))
    expect(screen.getByText('2 km')).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(800)
    })
    expect(push.setPreferences).toHaveBeenCalledWith(
      expect.objectContaining({ radios: { transito: 2000 } }),
    )
  })

  it('una categoría encendida no ofrece «Activar»', () => {
    render(<RadiosPorCategoria push={state({ transito: 3000 })} />)
    expect(screen.getByText('3 km')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Activar/ })).toBeNull()
  })
})
