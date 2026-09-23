/**
 * Reglas del radar de vehículos, sin React.
 *
 * Todo lo que decide QUÉ se muestra vive acá y no en los componentes. Así se
 * puede probar sin montar nada, y la tarjeta, el panel y el botón no se ponen
 * a discutir qué es «reciente».
 */

import type { VehicleFeedItem } from '@/api/vehicleFeedTypes'

const HOUR_MS = 3_600_000

/**
 * Ventana del feed. La misma que `FEED_MAX_HORAS` del backend.
 *
 * El backend ya filtra, pero el cliente vuelve a hacerlo por dos motivos: una
 * respuesta cacheada por el service worker puede tener horas, y una pestaña
 * abierta toda la tarde tiene que ver salir los avisos viejos aunque no haya
 * refetch. Por eso el filtro corre contra un reloj que avanza (`useNow`).
 */
export const FEED_WINDOW_HOURS = 48

/** Por debajo de esto, el aviso se resalta. */
export const RECENT_HOURS = 6

/** Tope de la consulta. Con la ventana de 48 h no se llega nunca en la práctica. */
export const FEED_LIMIT = 100

/**
 * Tolerancia a relojes adelantados.
 *
 * `detectado_en` lo pone el servidor, así que si el reloj del teléfono va
 * atrasado un aviso recién llegado puede «ser del futuro» por unos minutos. Eso
 * es normal y se acepta. Una hora en el futuro ya no es un desfase: es un dato
 * roto, y se descarta.
 */
const CLOCK_SKEW_MS = 10 * 60_000

/** Milisegundos de `detectado_en`, o `null` si no es una fecha. */
export function detectedAt(item: Pick<VehicleFeedItem, 'detectado_en'>): number | null {
  const ms = Date.parse(item.detectado_en)
  return Number.isFinite(ms) ? ms : null
}

/** ¿Lo vio AlertaV en las últimas 48 h? Una fecha inválida no está en ninguna ventana. */
export function withinWindow(
  item: Pick<VehicleFeedItem, 'detectado_en'>,
  now: number,
  hours = FEED_WINDOW_HOURS,
): boolean {
  const at = detectedAt(item)
  if (at === null) return false
  if (at - now > CLOCK_SKEW_MS) return false
  return now - at <= hours * HOUR_MS
}

/** ¿Lo vio AlertaV hace menos de 6 h? */
export function isRecent(item: Pick<VehicleFeedItem, 'detectado_en'>, now: number): boolean {
  const at = detectedAt(item)
  if (at === null) return false
  return now - at < RECENT_HOURS * HOUR_MS
}

/** Lo que el panel puede mostrar: dentro de la ventana, el más nuevo arriba. */
export function visibleItems(items: readonly VehicleFeedItem[], now: number): VehicleFeedItem[] {
  return items
    .filter((item) => withinWindow(item, now))
    .sort((a, b) => (detectedAt(b) ?? 0) - (detectedAt(a) ?? 0))
}

/**
 * `LKXV55` → `LK·XV·55`.
 *
 * El punto medio es como se escribe la patente en la placa. Los formatos son
 * los que acepta el parser del backend (`gbv_parser.py`):
 *
 *   - auto 2007+ `BBBB·10`  → `LK·XV·55`
 *   - auto antigua `AA·1000` → `YW·88·69`
 *   - moto `BBB·010`        → `RGT·012` (el backend rellena con cero)
 *
 * Cualquier otra cosa se devuelve tal cual: inventar un agrupamiento para un
 * formato que no se conoce sería peor que no agrupar.
 */
export function formatPatente(patente: string): string {
  const p = patente.toUpperCase()
  if (/^[A-Z]{4}\d{2}$/.test(p) || /^[A-Z]{2}\d{4}$/.test(p)) {
    return `${p.slice(0, 2)}·${p.slice(2, 4)}·${p.slice(4)}`
  }
  if (/^[A-Z]{3}\d{3}$/.test(p)) return `${p.slice(0, 3)}·${p.slice(3)}`
  return p
}

/** «Toyota Rav4», o el tipo si GBV no publicó marca ni modelo. */
export function vehicleTitle(item: VehicleFeedItem): string {
  const name = [item.marca, item.modelo].filter(Boolean).join(' ').trim()
  return name || item.tipo_vehiculo || 'Vehículo'
}

const MONTHS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']

/**
 * `2026-09-22` → `22 sep`.
 *
 * **No pasa por `new Date('2026-09-22')`.** Esa llamada interpreta la fecha
 * como medianoche UTC, que en Chile es la tarde del día ANTERIOR: el robo del
 * 22 se mostraría como del 21. Es una fecha de calendario sin hora, y se trata
 * como texto.
 */
export function formatCalendarDay(value: string | null, withYear = false): string | null {
  if (!value) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return null
  const [, year, month, day] = match
  const name = MONTHS[Number(month) - 1]
  if (!name) return null
  const text = `${Number(day)} ${name}`
  return withYear ? `${text} ${year}` : text
}

/** La referencia de fecha que va bajo el estado en la tarjeta cerrada. */
export function eventDateLabel(item: VehicleFeedItem): string | null {
  if (item.estado === 'abandonado') {
    return item.tiempo_abandono ? `hace ${item.tiempo_abandono}` : null
  }
  const day = formatCalendarDay(item.fecha_delito)
  // En un recuperado, `fecha_delito` sigue siendo la del robo, y se rotula así.
  return day ? `robo ${day}` : null
}
