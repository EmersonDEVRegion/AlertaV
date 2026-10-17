/**
 * Cómo se lee un aviso de la DMC en la interfaz.
 *
 * Violeta en tres intensidades (tokens `--dmc-*` en `index.css`), siempre con
 * la institución en el rótulo: «DMC · Aviso». Así un vistazo basta para no
 * confundirlo con una alerta de SENAPRED, que usa el semáforo.
 */

import type { MeteoWarning } from '@/api/meteoWarningTypes'

export interface WarningStyle {
  /** Fondo y tinta de la tarjeta. */
  card: string
  /** Borde izquierdo: la marca de severidad. */
  edge: string
  /** El rótulo «DMC · Aviso». */
  chip: string
}

export const WARNING_STYLE: Record<1 | 2 | 3, WarningStyle> = {
  1: {
    card: 'bg-dmc-1-bg text-dmc-1-ink',
    edge: 'border-l-dmc-1-line',
    chip: 'text-dmc-1-ink ring-1 ring-dmc-1-line',
  },
  2: {
    card: 'bg-dmc-2-bg text-dmc-2-ink',
    edge: 'border-l-dmc-2-line',
    chip: 'text-dmc-2-ink ring-1 ring-dmc-2-line',
  },
  3: {
    card: 'bg-dmc-3-bg text-dmc-3-ink',
    edge: 'border-l-dmc-3-line',
    chip: 'text-dmc-3-ink ring-1 ring-dmc-3-line',
  },
}

const DIAS: Record<string, string> = {
  lunes: 'lun',
  martes: 'mar',
  miercoles: 'mié',
  jueves: 'jue',
  viernes: 'vie',
  sabado: 'sáb',
  domingo: 'dom',
}

const TRAMOS: Record<string, string> = {
  madrugada: 'madrugada',
  manana: 'mañana',
  mediodia: 'mediodía',
  tarde: 'tarde',
  noche: 'noche',
}

function plain(text: string): string {
  return text
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
}

/**
 * «la tarde del lunes 19 de octubre del 2026» → «lun 19 tarde».
 *
 * La DMC no publica horas, sólo el tramo del día: se muestra el tramo, nunca
 * una hora inventada. Si el texto no se entiende, va tal cual.
 */
export function shortMoment(text: string | null): string | null {
  if (!text) return null
  const p = plain(text)
  const day = p.match(/\b(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\s+(\d{1,2})\b/)
  const weekday = DIAS[day?.[1] ?? '']
  if (!day || !weekday) return text
  const tramo = TRAMOS[p.match(/\b(madrugada|mediodia|manana|tarde|noche)\b/)?.[1] ?? '']
  const base = `${weekday} ${day[2]}`
  return tramo ? `${base} ${tramo}` : base
}

/** «En curso · hasta mar 20 noche» o «lun 19 tarde → mar 20 noche». */
export function vigenciaLabel(w: MeteoWarning): string {
  const desde = shortMoment(w.desde_texto)
  const hasta = shortMoment(w.hasta_texto)
  if (w.en_curso) return hasta ? `En curso · hasta ${hasta}` : 'En curso'
  if (desde && hasta) return `${desde} → ${hasta}`
  return desde ? `Desde ${desde}` : hasta ? `Hasta ${hasta}` : ''
}

/** «Toda la región» o «Litoral, Cordillera de la Costa y Precordillera y valles». */
export function zonasLabel(w: MeteoWarning): string {
  if (w.alcance === 'region' || w.zonas.length === 0) return 'Toda la región'
  const names = w.zonas.map((z) => z.nombre)
  const last = names.pop()
  return names.length === 0 ? (last ?? '') : `${names.join(', ')} y ${last}`
}

/** El fenómeno sin la palabra del nivel: la tarjeta ya lo dice. */
export function phenomenonLabel(w: MeteoWarning): string {
  return w.fenomeno ?? w.titulo ?? 'Fenómeno meteorológico'
}

/**
 * Colores de las zonas en el mapa, por severidad y tema. El mismo violeta que
 * los tokens `--dmc-*` de la franja, en hex porque MapLibre no lee variables
 * CSS. En oscuro se aclaran: un violeta oscuro sobre Dark Matter no se ve.
 */
export const METEO_ZONE_PALETTE: Record<'light' | 'dark', Record<1 | 2 | 3, string>> = {
  light: { 1: '#8b5cf6', 2: '#6d28d9', 3: '#4c1d95' },
  dark: { 1: '#a78bfa', 2: '#c4b5fd', 3: '#ede9fe' },
}

/** Opacidad del relleno: baja, porque la zona es contexto y no debe tapar el mapa. */
export const METEO_ZONE_FILL_OPACITY: Record<1 | 2 | 3, number> = { 1: 0.08, 2: 0.14, 3: 0.22 }
