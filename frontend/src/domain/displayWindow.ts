/**
 * Cuánto se queda una incidencia en el mapa, y qué pasa después.
 *
 * # Dos relojes distintos
 *
 * El motor de correlación tiene el suyo (`CORRELATION_STALE_HOURS`, 12 h): mide
 * «¿esto sigue siendo el mismo hecho?». Si una señal llega a un incidente
 * abierto, se le pega; si llega a uno cerrado, abre otro. Acortarlo partiría
 * incendios en dos y mandaría el mismo push dos veces.
 *
 * Éste mide otra cosa: «¿vale la pena seguir mostrándolo en el mapa?». Casi
 * todos los incidentes tienen UNA señal (la mediana de su vida es 0 h), así
 * que con el reloj del motor un despacho de Bomberos se quedaba 12 h en el
 * mapa por una sola observación. Acá cada familia tiene su ventana, contada
 * desde la última señal. Lo que sale del mapa no desaparece: baja al historial.
 *
 * # Los cortes de luz no usan ventana
 *
 * Las empresas no avisan cuándo reponen: dejan de listar el corte. El backend
 * compara la última vez que lo publicaron con su última lectura
 * (`outage.vigente`), y eso manda. La ventana sólo se usa si el backend no
 * sabe (`vigente: null`, sin ninguna lectura con qué comparar).
 */

import { COMUNAS } from '@/domain/comunas'
import type { Incident, IncidentStatus } from '@/api/types'
import { layerOf, type IncidentLayerKey } from './families'

const HOUR = 3_600_000

/** Horas en el mapa desde la última señal, por capa. */
export const DISPLAY_WINDOW_MS: Record<IncidentLayerKey, number> = {
  fire: 6 * HOUR,
  // Un choque se despeja en minutos u horas; a las dos ya es historia.
  traffic: 2 * HOUR,
  otros: 4 * HOUR,
  // Sólo como respaldo: ver `outage.vigente` arriba.
  power: 4 * HOUR,
}

/**
 * Lo que se le pide a `/incidents/active` para alimentar mapa e historial con
 * una sola consulta.
 *
 * El historial va desde las 00:00 de AYER (§I), o sea entre 24 y 48 h hacia
 * atrás según la hora. 50 y no 48: el día en que Chile atrasa la hora dura 25 h,
 * y con 48 justas a las 23:59 se perdería la primera hora de ayer.
 */
export const INCIDENT_QUERY_HOURS = 50

/** Las últimas 24 h, para el resumen «N en 24 h» de la columna. */
export const LAST_24H_MS = 24 * HOUR
export const INCIDENT_QUERY_STATUSES: IncidentStatus[] = [
  'active',
  'controlled',
  'stale',
  'extinguished',
]

/** Estados con los que un incidente puede estar en el mapa. */
const OPEN: ReadonlySet<IncidentStatus> = new Set(['active', 'controlled'])

const time = (iso: string): number => {
  const t = Date.parse(iso)
  return Number.isNaN(t) ? 0 : t
}

/**
 * Hasta cuándo va en el mapa. `Infinity` si no hay plazo (un corte que la
 * empresa sigue listando: sale cuando un sondeo diga lo contrario), y `-Infinity`
 * si ya no corresponde.
 */
export function mapUntil(incident: Incident): number {
  if (!OPEN.has(incident.status)) return -Infinity
  const layer = layerOf(incident.type)
  if (layer === 'power') {
    const vigente = incident.outage?.vigente
    if (vigente === true) return Infinity
    if (vigente === false) return -Infinity
  }
  return time(incident.last_seen_at) + DISPLAY_WINDOW_MS[layer]
}

export function isOnMap(incident: Incident, now: number): boolean {
  return mapUntil(incident) > now
}

/**
 * Hasta cuándo va en el historial: hasta que termine el día siguiente al de su
 * última señal (hora de Chile). Lo de ayer se ve completo, y no sólo la parte
 * que cae dentro de las últimas 24 h.
 *
 * # Por qué (§I, 15-10-2026)
 *
 * Con una ventana móvil de 24 h, la sección «Ayer» mostraba un pedazo de ayer
 * que cambiaba con la hora: a las 11:26, de 11:26 a 23:59; a las 23:00, una
 * sola hora. Se llamaba «Ayer» y no lo era.
 */
function historyUntil(incident: Incident): number {
  return startOfChileDay(addDays(chileDay(time(incident.last_seen_at)), 2))
}

export interface DisplaySplit {
  /** Lo que se dibuja y se cuenta como «activo». */
  onMap: Incident[]
  /** Todo lo de hoy y ayer (hora de Chile), esté o no en el mapa. */
  history: Incident[]
  /** El próximo instante en que alguno de los dos conjuntos cambia. */
  nextChange: number
}

/**
 * Reparte los incidentes y dice cuándo volver a mirar.
 *
 * `nextChange` es lo que permite no tener un reloj en `App`: en vez de
 * recalcular cada minuto (y repintar el mapa cada minuto), se agenda un único
 * temporizador para el momento exacto en que algo sale del mapa o del
 * historial.
 */
export function splitForDisplay(incidents: readonly Incident[], now: number): DisplaySplit {
  const onMap: Incident[] = []
  const history: Incident[] = []
  let nextChange = Infinity

  for (const incident of incidents) {
    const until = mapUntil(incident)
    if (until > now) {
      onMap.push(incident)
      if (until < nextChange) nextChange = until
    }
    const historyEnd = historyUntil(incident)
    // Un corte vigente de antes de ayer sigue en el historial mientras esté
    // en el mapa: sería raro verlo en uno y no en el otro.
    if (historyEnd > now || until > now) {
      history.push(incident)
      if (historyEnd > now && historyEnd < nextChange) nextChange = historyEnd
    }
  }

  return { onMap, history, nextChange }
}

// ---------------------------------------------------------------------------
// Historial: estado, grupos y secciones
// ---------------------------------------------------------------------------

export type HistoryState =
  | 'live'
  | 'controlled'
  | 'extinguished'
  | 'unlisted'
  | 'quiet'

/** Qué decir de cada fila. Nunca inventa un fin que nadie declaró. */
export const HISTORY_STATE_LABEL: Record<HistoryState, string> = {
  live: 'En curso',
  controlled: 'Controlado',
  extinguished: 'Extinguido',
  // La empresa dejó de listarlo. Casi siempre es que repuso, pero no lo dice:
  // por eso «probablemente» (§R-A; antes «Ya no figura», que no se entendía).
  unlisted: 'Probablemente repuesto',
  // `stale`, o una ventana vencida: dejaron de llegar señales. Nunca
  // «terminado»: nadie declaró el fin. `HistoryFeed` le agrega la hora.
  quiet: 'Sin novedades',
}

export function historyState(incident: Incident, onMap: boolean): HistoryState {
  if (incident.status === 'extinguished') return 'extinguished'
  if (onMap) return incident.status === 'controlled' ? 'controlled' : 'live'
  if (incident.status === 'controlled') return 'controlled'
  if (layerOf(incident.type) === 'power' && incident.outage?.vigente === false) {
    return 'unlisted'
  }
  return 'quiet'
}

interface HistorySingle {
  kind: 'single'
  key: string
  incident: Incident
  onMap: boolean
}

/** Varios cortes de luz de una misma comuna, que en una lista serían ruido. */
export interface HistoryOutageGroup {
  kind: 'outages'
  key: string
  commune: string | null
  incidents: Incident[]
  onMap: boolean
  /** Última señal del grupo. */
  latest: string
  /** Primera señal del grupo. */
  earliest: string
  /** Suma de clientes informados; `null` si ninguno lo informó. */
  clients: number | null
}

type HistoryEntry = HistorySingle | HistoryOutageGroup

export interface HistorySection {
  key: 'now' | 'services' | 'today' | 'yesterday' | 'earlier'
  label: string
  entries: HistoryEntry[]
  /** Incidentes en la sección (un grupo cuenta cada corte). */
  count: number
}

const SECTION_LABEL: Record<HistorySection['key'], string> = {
  // «En vivo» y no «En el mapa» (§I): lo que importa es que está pasando.
  now: 'En vivo',
  // §R: los cortes de luz en curso, aparte y plegados. Son servicios, no
  // emergencias, y ocupaban casi toda la sección «En vivo».
  services: 'Servicios interrumpidos',
  today: 'Hoy',
  yesterday: 'Ayer',
  earlier: 'Antes',
}

/** Día calendario en Chile (AAAA-MM-DD). `en-CA` escribe justo ese formato. */
const DAY = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/Santiago',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})
const chileDay = (t: number): string => DAY.format(new Date(t))

/** `AAAA-MM-DD` más `n` días, en calendario puro (sin husos). */
function addDays(day: string, n: number): string {
  const [y, m, d] = day.split('-').map(Number) as [number, number, number]
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10)
}

/**
 * El primer instante del día `AAAA-MM-DD` en Chile.
 *
 * Chile continental alterna UTC−3 y UTC−4, y los cambios de hora ocurren a
 * medianoche: el día en que se adelanta, las 00:00 no existen y el día empieza
 * a la 01:00. Por eso no se arma la hora a mano: se prueban los dos husos y se
 * queda el instante más temprano que ya cae en ese día.
 */
export function startOfChileDay(day: string): number {
  const [y, m, d] = day.split('-').map(Number) as [number, number, number]
  const candidates = [3, 4]
    .map((offset) => Date.UTC(y, m - 1, d, offset))
    .filter((t) => chileDay(t) === day)
  // Sin candidato sólo si cambiara el huso del país: medianoche en UTC−3.
  return candidates.length > 0 ? Math.min(...candidates) : Date.UTC(y, m - 1, d, 3)
}

function communeKey(commune: string | null): string {
  return (commune ?? '')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .trim()
    .toLowerCase()
}

/**
 * ¿El incidente es de esta comuna? Los feeds escriben «CONCON», «Concón» o
 * «VIÑA DEL MAR»: se comparan sin tildes ni mayúsculas.
 */
export function sameCommune(a: string | null, b: string | null): boolean {
  return a !== null && b !== null && communeKey(a) === communeKey(b)
}

const fold = (text: string) =>
  text
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()

const NO_COMMUNE = new Set(['unknown', 'desconocido', 'desconocida', 'sin informacion', 'sin comuna', 'n/a', 'null'])

/** Comunas por su nombre plegado, de la más larga a la más corta (La Calera antes que Calera). */
const COMUNAS_BY_FOLD: readonly (readonly [string, string])[] = COMUNAS.map(
  (c) => [fold(c.nombre), c.nombre] as const,
).sort((a, b) => b[0].length - a[0].length)

/**
 * El nombre de la comuna como se escribe: «VIÑA DEL MAR», «Quilpue» y
 * «VALPARAISO_ORIENTE» (zona de Chilquinta) pasan a «Viña del Mar», «Quilpué»
 * y «Valparaíso» (§R-A). Lo que no es una comuna queda en nombre propio.
 */
export function communeLabel(commune: string | null): string | null {
  if (!commune) return null
  const folded = fold(commune)
  // Lo que la distribuidora pone cuando no sabe (Chilquinta: «Unknown»).
  if (NO_COMMUNE.has(folded)) return null
  for (const [key, nombre] of COMUNAS_BY_FOLD) {
    if (folded === key || folded.startsWith(`${key} `)) return nombre
  }
  const clean = commune.replace(/_+/g, ' ')
  if (clean !== clean.toUpperCase()) return clean
  return clean
    .toLowerCase()
    .replace(/(^|[\s-])(\p{L})/gu, (_, sep: string, ch: string) => sep + ch.toUpperCase())
    .replace(/\b(De|Del|La|Las|Los|El)\b/g, (w, _m, offset: number) =>
      offset === 0 ? w : w.toLowerCase(),
    )
}

function groupOutages(incidents: Incident[], onMap: boolean): HistoryEntry[] {
  const entries: HistoryEntry[] = []
  const byCommune = new Map<string, Incident[]>()

  for (const incident of incidents) {
    if (layerOf(incident.type) !== 'power') {
      entries.push({ kind: 'single', key: incident.code, incident, onMap })
      continue
    }
    const key = communeKey(incident.commune)
    const group = byCommune.get(key)
    if (group) group.push(incident)
    else byCommune.set(key, [incident])
  }

  for (const [key, group] of byCommune) {
    if (group.length === 1) {
      entries.push({ kind: 'single', key: group[0]!.code, incident: group[0]!, onMap })
      continue
    }
    let latest = group[0]!.last_seen_at
    let earliest = group[0]!.first_seen_at
    let clients: number | null = null
    for (const incident of group) {
      if (time(incident.last_seen_at) > time(latest)) latest = incident.last_seen_at
      if (time(incident.first_seen_at) < time(earliest)) earliest = incident.first_seen_at
      const c = incident.outage?.affected_clients
      if (typeof c === 'number') clients = (clients ?? 0) + c
    }
    entries.push({
      kind: 'outages',
      key: `luz:${key}:${onMap ? 'mapa' : 'hist'}`,
      commune: communeLabel(group[0]!.commune),
      incidents: group,
      onMap,
      latest,
      earliest,
      clients,
    })
  }

  const latestOf = (e: HistoryEntry) =>
    time(e.kind === 'single' ? e.incident.last_seen_at : e.latest)
  return entries.sort((a, b) => latestOf(b) - latestOf(a))
}

/** Un servicio interrumpido (hoy, cortes de luz), no una emergencia. */
export function isService(incident: Pick<Incident, 'type'>): boolean {
  return incident.type === 'power_outage'
}

/** Las secciones de cada pestaña de la columna (§R). */
export const NOW_SECTIONS: readonly HistorySection['key'][] = ['now', 'services']
export const PAST_SECTIONS: readonly HistorySection['key'][] = ['today', 'yesterday', 'earlier']

/**
 * El historial en secciones: lo que está en el mapa arriba, y el resto por día.
 * Los cortes de luz de una misma comuna se juntan en una fila.
 */
export function historySections(
  history: readonly Incident[],
  onMapCodes: ReadonlySet<string>,
  now: number,
): HistorySection[] {
  const today = chileDay(now)
  // Por calendario y no restando 24 h: el día del cambio de hora dura 23 o 25.
  const yesterday = addDays(today, -1)
  const buckets: Record<HistorySection['key'], Incident[]> = {
    now: [],
    services: [],
    today: [],
    yesterday: [],
    earlier: [],
  }

  for (const incident of history) {
    if (onMapCodes.has(incident.code)) {
      ;(isService(incident) ? buckets.services : buckets.now).push(incident)
      continue
    }
    const day = chileDay(time(incident.last_seen_at))
    if (day === today) buckets.today.push(incident)
    else if (day === yesterday) buckets.yesterday.push(incident)
    else buckets.earlier.push(incident)
  }

  return (Object.keys(buckets) as HistorySection['key'][])
    .filter((key) => buckets[key].length > 0)
    .map((key) => ({
      key,
      label: SECTION_LABEL[key],
      entries: groupOutages(buckets[key], key === 'now' || key === 'services'),
      count: buckets[key].length,
    }))
}
