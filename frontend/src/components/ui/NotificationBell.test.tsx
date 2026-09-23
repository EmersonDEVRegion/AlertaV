import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { PushNotificationsState } from '@/hooks/usePushNotifications'
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
    preferences: { notifyIncidents: true, notifySeismic: true },
    locationSyncedAt: null,
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

    expect(screen.getByText(/Emergencias a menos de 5 km/)).toBeInTheDocument()
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
    const push = state({ phase: 'on', locationSyncedAt: Date.now() - 5 * 60_000 })
    render(<NotificationPanel push={push} />)

    expect(screen.getByText(/Ubicación informada hace 5 minutos/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('switch', { name: PUSH_TEXT.seismic }))
    expect(push.setPreferences).toHaveBeenCalledWith({
      notifyIncidents: true,
      notifySeismic: false,
    })

    await userEvent.click(screen.getByRole('button', { name: PUSH_TEXT.probe }))
    expect(push.sendProbe).toHaveBeenCalledOnce()

    await userEvent.click(screen.getByRole('button', { name: PUSH_TEXT.disable }))
    expect(push.disable).toHaveBeenCalledOnce()
  })

  it('no deja apagar la última preferencia: para eso está desactivar', () => {
    render(
      <NotificationPanel
        push={state({
          phase: 'on',
          preferences: { notifyIncidents: true, notifySeismic: false },
        })}
      />,
    )
    expect(screen.getByRole('switch', { name: PUSH_TEXT.incidents })).toBeDisabled()
    expect(screen.getByRole('switch', { name: PUSH_TEXT.seismic })).toBeEnabled()
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
