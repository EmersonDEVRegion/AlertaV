import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Incident } from '@/api/types'
import { makeIncident } from '@/test/fixtures'
import { resetSelection, selectIncident } from '@/lib/selectionStore'
import { act } from '@testing-library/react'
import { resetExplore, setExploreArea } from '@/lib/exploreStore'
import { resetSections } from '@/lib/sectionStore'
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
    resetExplore()
    resetSections()
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

    const now = screen.getByRole('group', { name: 'En vivo' })
    const today = screen.getByRole('group', { name: 'Hoy' })
    expect(within(now).getByText('Incendio estructural — Concón')).toBeInTheDocument()
    // «En curso» no se repite en cada fila: ya lo dice la sección.
    expect(within(now).queryByText('En curso')).not.toBeInTheDocument()
    expect(within(today).getByText('Accidente — Viña del Mar')).toBeInTheDocument()
    // «Sin novedades», nunca «terminado»: nadie declaró el fin.
    expect(within(today).getByText(/^Sin novedades desde \d{2}:\d{2}$/)).toBeInTheDocument()
  })

  it('tocar una fila la enfoca', async () => {
    vi.useRealTimers()
    const user = userEvent.setup()
    const { onFocus } = renderFeed([LIVE], [LIVE.code])
    await user.click(screen.getByRole('button', { name: /concón/i }))
    expect(onFocus).toHaveBeenCalledWith(LIVE)
  })

  it('junta los cortes de una comuna y los despliega al tocar', async () => {
    const user = userEvent.setup()
    renderFeed([
      outage('INC-1', 2, 'QUILPUE', 300),
      outage('INC-2', 3, 'Quilpué', 120),
    ])

    const group = screen.getByRole('button', { name: /2 cortes de luz — quilpué/i })
    expect(group).toHaveTextContent('420 clientes')
    expect(group).toHaveTextContent('Probablemente repuesto')
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
    expect(screen.getByText(/sin emergencias hoy ni ayer/i)).toBeInTheDocument()
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
  it('acotado a una comuna muestra el chip y sólo lo de ahí; el chip lo quita', async () => {
    const concon = makeIncident({
      code: 'INC-2026-00690',
      commune: 'Concón',
      title: 'Incendio forestal — Concón',
      last_seen_at: ago(1),
    })
    setExploreArea({ kind: 'commune', name: 'CONCON' })
    renderFeed([OLD, concon], [concon.code])

    expect(screen.getByText('Incendio forestal — Concón')).toBeInTheDocument()
    expect(screen.queryByText('Accidente — Viña del Mar')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Quitar el filtro: Concón/i }))
    expect(screen.getByText('Accidente — Viña del Mar')).toBeInTheDocument()
  })

  it('un área sin nada lo dice sin culpar a las capas', () => {
    act(() => setExploreArea({ kind: 'place', id: 'a', name: 'Casa', lat: -32.5, lon: -71.4 }))
    renderFeed([OLD, LIVE], [LIVE.code])
    expect(screen.getByText('Sin emergencias hoy ni ayer cerca de Casa.')).toBeInTheDocument()
  })

  describe('secciones plegables', () => {
    // 19:00 − 20 h = 23:00 de ayer en Chile.
    const YESTERDAY = makeIncident({
      code: 'INC-2026-00500',
      type: 'accident',
      status: 'stale',
      title: 'Accidente — Quilpué',
      last_seen_at: ago(20),
    })

    it('En vivo y Hoy abiertas; Ayer plegada, con su conteo a la vista', () => {
      renderFeed([OLD, LIVE, YESTERDAY], [LIVE.code])
      expect(screen.getByRole('button', { name: /En vivo/ })).toHaveAttribute('aria-expanded', 'true')
      expect(screen.getByRole('button', { name: /Hoy/ })).toHaveAttribute('aria-expanded', 'true')
      const ayer = screen.getByRole('button', { name: /Ayer/ })
      expect(ayer).toHaveAttribute('aria-expanded', 'false')
      expect(ayer).toHaveTextContent('1')
      expect(screen.queryByText('Accidente — Quilpué')).not.toBeInTheDocument()
    })

    it('tocar el encabezado pliega y despliega, y se recuerda', async () => {
        const user = userEvent.setup()
      const { unmount } = render(
        <HistoryFeed
          history={[OLD, LIVE, YESTERDAY]}
          onMapCodes={new Set([LIVE.code])}
          onFocus={vi.fn()}
          ready
        />,
      )
      await user.click(screen.getByRole('button', { name: /Ayer/ }))
      expect(screen.getByText('Accidente — Quilpué')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: /En vivo/ }))
      expect(screen.queryByText('Incendio estructural — Concón')).not.toBeInTheDocument()

      // Al volver a abrir la pestaña quedan como se dejaron.
      unmount()
      renderFeed([OLD, LIVE, YESTERDAY], [LIVE.code])
      expect(screen.getByText('Accidente — Quilpué')).toBeInTheDocument()
      expect(screen.queryByText('Incendio estructural — Concón')).not.toBeInTheDocument()
    })

    it('una sección plegada se abre sola si lo seleccionado está ahí', () => {
      selectIncident(YESTERDAY.code)
      renderFeed([OLD, LIVE, YESTERDAY], [LIVE.code])
      expect(screen.getByRole('button', { name: /Ayer/ })).toHaveAttribute('aria-expanded', 'true')
      expect(screen.getByText('Accidente — Quilpué')).toBeInTheDocument()
    })
  })
})
