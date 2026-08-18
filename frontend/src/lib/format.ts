/** Formato de fechas y numeros en es-CL, hora de Chile continental. */

const TZ = 'America/Santiago'

const timeFmt = new Intl.DateTimeFormat('es-CL', {
  hour: '2-digit',
  minute: '2-digit',
  timeZone: TZ,
})

const dateTimeFmt = new Intl.DateTimeFormat('es-CL', {
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: TZ,
})

const relativeFmt = new Intl.RelativeTimeFormat('es-CL', { numeric: 'auto' })

export function formatTime(iso: string | number | Date): string {
  return timeFmt.format(new Date(iso))
}

export function formatDateTime(iso: string | number | Date): string {
  return dateTimeFmt.format(new Date(iso))
}

/** "hace 4 minutos", "hace 2 horas". */
export function formatRelative(iso: string | number | Date, now = Date.now()): string {
  const deltaMs = new Date(iso).getTime() - now
  const abs = Math.abs(deltaMs)
  const minute = 60_000
  const hour = 60 * minute
  const day = 24 * hour

  if (abs < minute) return 'recién'
  if (abs < hour) return relativeFmt.format(Math.round(deltaMs / minute), 'minute')
  if (abs < day) return relativeFmt.format(Math.round(deltaMs / hour), 'hour')
  return relativeFmt.format(Math.round(deltaMs / day), 'day')
}

/** 0.964 -> "96 %". Se trunca hacia abajo: redondear 0.996 a 100 % mentiria. */
export function formatPercent(value: number): string {
  return `${Math.floor(value * 100)} %`
}

export function formatDistance(metres: number): string {
  return metres < 1000
    ? `${Math.round(metres)} m`
    : `${(metres / 1000).toFixed(1)} km`
}
