import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Incident } from '@/api/types'
import { makeIncident } from '@/test/fixtures'
import { resetSelection, selectIncident } from '@/lib/selectionStore'
import { HistoryFeed } from './HistoryFeed'

/** 29-sep-2026, 19:00 en Chile. */
const NOW = Date.parse('2026-09-29T22:00:00Z')
const ago = (h: number) => new Date(NOW - h * 3_600_000).toISOString()

function outage(code: string, hoursAgo: number, commune: string, clients: number): Incident {
  return makeIncident({
    code,
    type: 'power_outage',
    commune,
    title: `Corte de suministro — ${commune}`,
    last_seen_at: ago(hoursAgo),
    first_seen_at: ago(hoursAgo),
    outage: {
      provider: 'chilquinta',
      affected_clients: clients,
      estimated_restoration: null,
      sector: null,
      outage_count: 1,
      vigente: false,
    },
  })
}

const LIVE = makeIncident({
  code: 'INC-2026-00681',
  type: 'structural_fire',
  title: 'Incendio estructural — Concón',
  last_seen_at: ago(1),
})
const OLD = makeIncident({
  code: 'INC-2026-00600',
  type: 'accident',
  status: 'stale',
  title: 'Accidente — Viña del Mar',
  last_seen_at: ago(9),
})

describe('historial', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
  })
  afterEach(() => {
    vi.useRealTimers()
    resetSelection()
  })

  function renderFeed(history: Incident[], onMap: string[] = [], over = {}) {
    const onFocus = vi.fn()
    render(
      <HistoryFeed
        history={history}
        onMapCodes={new Set(onMap)}
        onFocus={onFocus}
        ready
        {...over}
      />,
    )
    return { onFocus }
  }

  it('separa lo que está en el mapa de lo que ya salió, con su estado', () => {
    renderFeed([OLD, LIVE], [LIVE.code])

    const now = screen.getByRole('group', { name: 'En el mapa' })
    const today = screen.getByRole('group', { name: 'Hoy' })
    expect(within(now).getByText('Incendio estructural — Concón')).toBeInTheDocument()
    // «En curso» no se repite en cada fila: ya lo dice la sección.
    expect(within(now).queryByText('En curso')).not.toBeInTheDocument()
    expect(within(today).getByText('Accidente — Viña del Mar')).toBeInTheDocument()
    // «Sin novedades», nunca «terminado»: nadie declaró el fin.
    expect(within(today).getByText('Sin novedades')).toBeInTheDocument()
  })

  it('tocar una fila la enfoca', async () => {
    vi.useRealTimers()
    const user = userEvent.setup()
    const { onFocus } = renderFeed([LIVE], [LIVE.code])
    await user.click(screen.getByRole('button', { name: /concón/i }))
    expect(onFocus).toHaveBeenCalledWith(LIVE)
  })

  it('junta los cortes de una comuna y los despliega al tocar', async () => {
    vi.useRealTimers()
    const user = userEvent.setup()
    renderFeed([
      outage('INC-1', 2, 'QUILPUE', 300),
      outage('INC-2', 3, 'Quilpué', 120),
    ])

    const group = screen.getByRole('button', { name: /2 cortes de luz — quilpue/i })
    expect(group).toHaveTextContent('420 clientes')
    expect(group).toHaveTextContent('Ya no figura')
    expect(screen.queryByText('300 clientes')).not.toBeInTheDocument()

    await user.click(group)
    expect(screen.getByText('300 clientes')).toBeInTheDocument()
  })

  it('un grupo con el corte seleccionado se abre solo', () => {
    selectIncident('INC-2')
    renderFeed([outage('INC-1', 2, 'Limache', 1), outage('INC-2', 3, 'Limache', 1)])
    expect(screen.getByRole('button', { name: /2 cortes de luz/i })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
  })

  it('vacío con las fuentes al día: lo dice', () => {
    renderFeed([], [], {
      health: { generated_at: '', by_family: { fire: 'ok' }, collectors: [] },
    })
    expect(screen.getByText(/sin emergencias en las últimas 24 h/i)).toBeInTheDocument()
  })

  it('vacío con una fuente caída: no lo vende como calma', () => {
    renderFeed([], [], {
      health: { generated_at: '', by_family: { fire: 'failing' }, collectors: [] },
    })
    expect(screen.getByText(/el vacío puede no ser calma/i)).toBeInTheDocument()
  })

  it('antes de la primera respuesta no afirma nada', () => {
    renderFeed([], [], { ready: false })
    expect(screen.queryByText(/sin emergencias/i)).not.toBeInTheDocument()
  })
})
