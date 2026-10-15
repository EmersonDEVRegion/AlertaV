import { describe, expect, it } from 'vitest'
import type { Incident } from '@/api/types'
import { makeIncident } from '@/test/fixtures'
import {
  DISPLAY_WINDOW_MS,
  communeLabel,
  historySections,
  historyState,
  isOnMap,
  isService,
  mapUntil,
  splitForDisplay,
  startOfChileDay,
} from './displayWindow'

/** 29-sep-2026, 19:00 en Chile (UTC-3). */
const NOW = Date.parse('2026-09-29T22:00:00Z')
const H = 3_600_000
const ago = (hours: number) => new Date(NOW - hours * H).toISOString()

let seq = 0
function inc(over: Partial<Incident>): Incident {
  seq += 1
  return makeIncident({ code: `INC-2026-${String(seq).padStart(5, '0')}`, ...over })
}

function outage(hoursAgo: number, vigente: boolean | null, commune = 'Quilpué'): Incident {
  return inc({
    type: 'power_outage',
    commune,
    last_seen_at: ago(hoursAgo),
    first_seen_at: ago(hoursAgo),
    outage: {
      provider: 'chilquinta',
      affected_clients: 100,
      estimated_restoration: null,
      sector: null,
      outage_count: 1,
      vigente,
    },
  })
}

describe('ventana de exhibición', () => {
  it('cada familia tiene la suya, contada desde la última señal', () => {
    expect(isOnMap(inc({ type: 'structural_fire', last_seen_at: ago(5.9) }), NOW)).toBe(true)
    expect(isOnMap(inc({ type: 'structural_fire', last_seen_at: ago(6.1) }), NOW)).toBe(false)
    expect(isOnMap(inc({ type: 'accident', last_seen_at: ago(1.9) }), NOW)).toBe(true)
    expect(isOnMap(inc({ type: 'accident', last_seen_at: ago(2.1) }), NOW)).toBe(false)
    expect(isOnMap(inc({ type: 'rescue', last_seen_at: ago(3.9) }), NOW)).toBe(true)
    expect(isOnMap(inc({ type: 'rescue', last_seen_at: ago(4.1) }), NOW)).toBe(false)
  })

  it('un incidente sin señales o extinguido no va al mapa aunque sea reciente', () => {
    expect(isOnMap(inc({ status: 'stale', last_seen_at: ago(0.1) }), NOW)).toBe(false)
    expect(isOnMap(inc({ status: 'extinguished', last_seen_at: ago(0.1) }), NOW)).toBe(false)
    expect(isOnMap(inc({ status: 'controlled', last_seen_at: ago(0.1) }), NOW)).toBe(true)
  })

  it('un corte de luz se queda mientras la empresa lo liste, sin importar la hora', () => {
    expect(mapUntil(outage(30, true))).toBe(Infinity)
    expect(isOnMap(outage(0.1, false), NOW)).toBe(false)
  })

  it('sin dato de vigencia, el corte usa la ventana de respaldo', () => {
    expect(isOnMap(outage(3.9, null), NOW)).toBe(true)
    expect(isOnMap(outage(DISPLAY_WINDOW_MS.power / H + 0.1, null), NOW)).toBe(false)
  })
})

describe('reparto entre mapa e historial', () => {
  it('lo que sale del mapa queda en el historial hasta el fin del día siguiente', () => {
    // NOW es el 29-09 a las 19:00. Ayer es el 28-09 entero, desde las 00:00.
    const fresh = inc({ type: 'structural_fire', last_seen_at: ago(1) })
    const old = inc({ type: 'structural_fire', last_seen_at: ago(8) })
    // 28-09 a las 18:00: con la ventana móvil de 24 h ya se habría ido (§I).
    const yesterday = inc({ type: 'structural_fire', last_seen_at: ago(25) })
    // 28-09 a las 00:30: el primer rato de ayer también.
    const earlyYesterday = inc({ type: 'structural_fire', last_seen_at: ago(42.5) })
    // 27-09 a las 23:30: antes de ayer, fuera.
    const gone = inc({ type: 'structural_fire', last_seen_at: ago(43.5) })

    const { onMap, history } = splitForDisplay(
      [fresh, old, yesterday, earlyYesterday, gone],
      NOW,
    )

    expect(onMap).toEqual([fresh])
    expect(history).toEqual([fresh, old, yesterday, earlyYesterday])
  })

  it('lo de ayer sale del historial a la medianoche, no a las 24 h', () => {
    const yesterday = inc({ type: 'structural_fire', last_seen_at: ago(25) })
    const midnight = Date.parse('2026-09-30T03:00:00Z') // 30-09 00:00 en Chile
    expect(splitForDisplay([yesterday], midnight - 1).history).toEqual([yesterday])
    expect(splitForDisplay([yesterday], midnight).history).toEqual([])
    expect(splitForDisplay([yesterday], NOW).nextChange).toBe(midnight)
  })

  it('un corte vigente de antes de ayer sigue también en el historial', () => {
    const long = outage(50, true)
    const { onMap, history } = splitForDisplay([long], NOW)
    expect(onMap).toEqual([long])
    expect(history).toEqual([long])
  })

  it('dice cuándo es el próximo cambio, para no tener un reloj en App', () => {
    const fire = inc({ type: 'structural_fire', last_seen_at: ago(5) }) // sale en 1 h
    const crash = inc({ type: 'accident', last_seen_at: ago(1.5) }) // sale en 30 min
    const { nextChange } = splitForDisplay([fire, crash], NOW)
    expect(nextChange).toBe(NOW + 0.5 * H)
  })

  it('sin nada que vaya a cambiar, no agenda nada', () => {
    expect(splitForDisplay([], NOW).nextChange).toBe(Infinity)
  })
})

describe('inicio del día en Chile', () => {
  it('en horario de verano (UTC−3) es a las 03:00 UTC', () => {
    expect(startOfChileDay('2026-10-15')).toBe(Date.parse('2026-10-15T03:00:00Z'))
  })

  it('en horario de invierno (UTC−4) es a las 04:00 UTC', () => {
    expect(startOfChileDay('2026-07-01')).toBe(Date.parse('2026-07-01T04:00:00Z'))
  })

  it('el día en que se adelanta la hora empieza a la 01:00, que es lo primero que existe', () => {
    // 6-sep-2026: a las 00:00 (UTC−4) los relojes saltan a la 01:00 (UTC−3).
    expect(startOfChileDay('2026-09-06')).toBe(Date.parse('2026-09-06T04:00:00Z'))
  })
})

describe('estado de una fila del historial', () => {
  it('no inventa un fin que nadie declaró', () => {
    expect(historyState(inc({ status: 'active' }), true)).toBe('live')
    expect(historyState(inc({ status: 'active' }), false)).toBe('quiet')
    expect(historyState(inc({ status: 'stale' }), false)).toBe('quiet')
    expect(historyState(inc({ status: 'controlled' }), false)).toBe('controlled')
    expect(historyState(inc({ status: 'extinguished' }), false)).toBe('extinguished')
    expect(historyState(outage(1, false), false)).toBe('unlisted')
  })
})

describe('secciones', () => {
  it('arriba lo del mapa, después hoy y ayer en hora de Chile', () => {
    const live = inc({ type: 'structural_fire', last_seen_at: ago(1) })
    // 19:00 − 10 h = 09:00 de hoy en Chile
    const today = inc({ type: 'structural_fire', last_seen_at: ago(10) })
    // 19:00 − 20 h = 23:00 de ayer en Chile
    const yesterday = inc({ type: 'accident', last_seen_at: ago(20) })

    const sections = historySections(
      [yesterday, today, live],
      new Set([live.code]),
      NOW,
    )

    expect(sections.map((s) => [s.key, s.count])).toEqual([
      ['now', 1],
      ['today', 1],
      ['yesterday', 1],
    ])
    // §I: «En vivo», no «En el mapa».
    expect(sections.map((s) => s.label)).toEqual(['En vivo', 'Hoy', 'Ayer'])
  })

  it('ayer es el día calendario entero, también su primera hora', () => {
    // 19:00 − 42,5 h = 00:30 de ayer en Chile.
    const early = inc({ type: 'accident', last_seen_at: ago(42.5) })
    const [section] = historySections([early], new Set(), NOW)
    expect(section!.key).toBe('yesterday')
  })

  it('junta los cortes de luz de una misma comuna y suma sus clientes', () => {
    const a = outage(3, false, 'QUILPUE')
    const b = outage(5, false, 'Quilpué')
    const c = outage(4, false, 'Limache')
    const fire = inc({ type: 'wildfire', last_seen_at: ago(7) })

    const [today] = historySections([a, b, c, fire], new Set(), NOW)
    const group = today!.entries.find((e) => e.kind === 'outages')

    expect(today!.count).toBe(4)
    expect(today!.entries).toHaveLength(3)
    expect(group).toMatchObject({ kind: 'outages', clients: 200, latest: a.last_seen_at })
    // El más reciente arriba.
    expect(today!.entries[0]).toBe(group)
  })

  it('los cortes de luz en el mapa van a «Servicios interrumpidos», no a «En vivo»', () => {
    const live = inc({ type: 'wildfire', last_seen_at: ago(1) })
    const cut = outage(2, true)
    const sections = historySections([live, cut], new Set([live.code, cut.code]), NOW)

    expect(sections.map((s) => [s.key, s.count])).toEqual([
      ['now', 1],
      ['services', 1],
    ])
    expect(sections[1]!.label).toBe('Servicios interrumpidos')
    expect(isService(cut)).toBe(true)
    expect(isService(live)).toBe(false)
  })

  it('un corte solo no se agrupa', () => {
    const [section] = historySections([outage(1, true)], new Set(), NOW)
    expect(section!.entries[0]!.kind).toBe('single')
  })
})

describe('nombre de comuna', () => {
  it('pasa las mayúsculas del feed a nombre propio', () => {
    expect(communeLabel('VIÑA DEL MAR')).toBe('Viña del Mar')
    expect(communeLabel('SAN ANTONIO')).toBe('San Antonio')
    expect(communeLabel('Concón')).toBe('Concón')
    expect(communeLabel(null)).toBeNull()
  })
})
