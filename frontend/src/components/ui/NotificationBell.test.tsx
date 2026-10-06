import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { PushNotificationsState } from '@/hooks/usePushNotifications'
import { resetPlaces } from '@/lib/placesStore'
import { NotificationPanel, PUSH_TEXT } from './NotificationBell'

function state(overrides: Partial<PushNotificationsState> = {}): PushNotificationsState {
  return {
    phase: 'off',
    step: null,
    message: null,
    server: {
      enabled: true,
      public_key: 'BP4z',
      reason: null,
      incident_radius_m: 5000,
      incident_min_confidence: 0.3,
      incident_min_sources: 2,
      seismic_min_magnitude: 3.5,
    },
    preferences: { notifyIncidents: true, notifySeismic: true, radios: {} },
    locatedAt: null,
    probeResult: null,
    enable: vi.fn(async () => undefined),
    disable: vi.fn(async () => undefined),
    setPreferences: vi.fn(async () => undefined),
    refreshLocation: vi.fn(async () => undefined),
    sendProbe: vi.fn(async () => undefined),
    ...overrides,
  }
}

describe('NotificationPanel', () => {
  it('apagado: explica qué se avisa con los umbrales del servidor y ofrece activar', async () => {
    const push = state()
    render(<NotificationPanel push={push} />)

    expect(screen.getByText(/Emergencias cerca de ti/)).toBeInTheDocument()
    expect(screen.getByText(/incendios a menos de 5 km/)).toBeInTheDocument()
    expect(screen.getByText(/al menos 2 fuentes distintas/)).toBeInTheDocument()
    expect(screen.getByText(/magnitud 3,5 o más/)).toBeInTheDocument()
    expect(screen.getByText(/última ubicación que la app conoce/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: PUSH_TEXT.enable }))
    expect(push.enable).toHaveBeenCalledOnce()
  })

  it('mientras trabaja dice qué está esperando', () => {
    render(<NotificationPanel push={state({ phase: 'working', step: 'location' })} />)
    expect(screen.getByRole('button', { name: /Obteniendo tu ubicación/ })).toBeDisabled()
  })

  it('encendido: preferencias, prueba y baja', async () => {
    const push = state({ phase: 'on', locatedAt: Date.now() - 5 * 60_000 })
    render(<NotificationPanel push={push} />)

    expect(screen.getByText(/Tu ubicación es de hace 5 minutos/)).toBeInTheDocument()
    // En jsdom no hay puntero táctil: es un computador, y lo dice.
    expect(screen.getByText('Activos en este computador')).toBeInTheDocument()
    expect(screen.getByText(/Guarda tu casa o tu trabajo/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('switch', { name: PUSH_TEXT.seismic }))
    expect(push.setPreferences).toHaveBeenCalledWith({
      notifyIncidents: true,
      notifySeismic: false,
      radios: {},
    })

    await userEvent.click(screen.getByRole('button', { name: PUSH_TEXT.probe }))
    expect(push.sendProbe).toHaveBeenCalledOnce()

    await userEvent.click(screen.getByRole('button', { name: PUSH_TEXT.disable }))
    expect(push.disable).toHaveBeenCalledOnce()
  })

  it('un deslizador por categoría, con el radio del servidor, y guarda solo', async () => {
    vi.useFakeTimers()
    try {
      const push = state({ phase: 'on', locatedAt: Date.now() })
      render(<NotificationPanel push={push} />)

      const luz = screen.getByRole('slider', { name: 'Cortes de luz' })
      expect(luz).toHaveAttribute('aria-valuetext', '1 km')
      expect(screen.getByRole('slider', { name: 'Incendios' })).toHaveAttribute(
        'aria-valuetext',
        '5 km',
      )
      expect(screen.getAllByRole('slider')).toHaveLength(6)

      // Todo a la izquierda: no avisar.
      fireEvent.change(luz, { target: { value: '0' } })
      expect(luz).toHaveAttribute('aria-valuetext', 'No avisar')
      expect(push.setPreferences).not.toHaveBeenCalled()

      await act(async () => {
        vi.advanceTimersByTime(800)
      })
      expect(push.setPreferences).toHaveBeenCalledWith({
        notifyIncidents: true,
        notifySeismic: true,
        radios: { power: 0 },
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('no deja apagar la última preferencia: para eso está desactivar', () => {
    render(
      <NotificationPanel
        push={state({
          phase: 'on',
          preferences: { notifyIncidents: true, notifySeismic: false, radios: {} },
        })}
      />,
    )
    expect(screen.getByRole('switch', { name: PUSH_TEXT.incidents })).toBeDisabled()
    expect(screen.getByRole('switch', { name: PUSH_TEXT.seismic })).toBeEnabled()
  })

  it('una ubicación de hace días se nota, y los lugares guardados se nombran', () => {
    localStorage.setItem(
      'alertav:lugares',
      JSON.stringify([
        { id: 'a', name: 'Casa', lat: -33.04, lon: -71.44 },
        { id: 'b', name: 'Trabajo', lat: -33.02, lon: -71.55 },
      ]),
    )
    resetPlaces()
    render(
      <NotificationPanel push={state({ phase: 'on', locatedAt: Date.now() - 3 * 86_400_000 })} />,
    )
    expect(screen.getByText(/Tu ubicación es de hace 3 días/)).toHaveTextContent(
      /Si ya no estás ahí/,
    )
    expect(screen.getByText('También te avisamos cerca de Casa y Trabajo.')).toBeInTheDocument()
    localStorage.removeItem('alertav:lugares')
    resetPlaces()
  })

  it('en un computador explica que la ubicación es aproximada', () => {
    render(<NotificationPanel push={state()} />)
    expect(screen.getByText(PUSH_TEXT.desktopNote)).toBeInTheDocument()
  })

  it('bloqueado: dice dónde desbloquearlo', () => {
    render(<NotificationPanel push={state({ phase: 'denied' })} />)
    expect(screen.getByText(PUSH_TEXT.denied)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: PUSH_TEXT.enable })).toBeNull()
  })

  it('iPhone sin instalar: explica cómo instalarla', () => {
    render(<NotificationPanel push={state({ phase: 'ios-install' })} />)
    expect(screen.getByText(PUSH_TEXT.iosInstall)).toBeInTheDocument()
  })

  it('sin configuración en el servidor: muestra el motivo', () => {
    render(
      <NotificationPanel
        push={state({ phase: 'unavailable', message: 'Los avisos no están configurados.' })}
      />,
    )
    expect(screen.getByText('Los avisos no están configurados.')).toBeInTheDocument()
    expect(screen.queryByText(PUSH_TEXT.seismicCaveat)).toBeNull()
  })

  it('avisa que los temblores no son alerta temprana', () => {
    render(<NotificationPanel push={state()} />)
    expect(screen.getByText(PUSH_TEXT.seismicCaveat)).toBeInTheDocument()
  })

  it('con los avisos pausados en el servidor lo dice', () => {
    const push = state({ phase: 'on' })
    push.server = { ...push.server!, enabled: false }
    render(<NotificationPanel push={push} />)
    expect(screen.getByText(/pausados en el servidor/)).toBeInTheDocument()
  })

  it('muestra el error del último intento', () => {
    render(<NotificationPanel push={state({ message: 'Sin tu ubicación no podemos…' })} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Sin tu ubicación')
  })
})
