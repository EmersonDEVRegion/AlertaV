/**
 * Simbología del mapa. Modulo único y deliberadamente pequeno.
 *
 * El backend expone DOS ejes que no se pueden colapsar en un color:
 *
 *   - `confidence` / `is_official_confirmed`  ->  cuan seguros estamos de que el
 *     FENOMENO existe.
 *   - `alert_level` / `alert_confidence`      ->  que declaro SENAPRED.
 *
 * Un incidente puede tener alerta roja vigente y no estar confirmado por CONAF.
 * Pintar eso de un solo color obligaria a mentir en uno de los dos ejes.
 *
 * Decisión: **relleno = fenómeno, halo/anillo = alerta oficial**.
 *
 * Todo lo que colorea la app sale de aquí: el mapa, la leyenda y la tarjeta de
 * detalle. Se pone la clave calculada dentro de las propiedades del GeoJSON para
 * que la expresión de MapLibre sea un `match` sobre esa clave y no una segunda
 * copia de las reglas. Asi el mapa y la leyenda no pueden discrepar.
 */

import type { AlertLevel, Incident, IncidentStatus } from '@/api/types'

// ---------------------------------------------------------------------------
// Eje 1: el fenómeno
// ---------------------------------------------------------------------------

export type PhenomenonKey =
  /** Una fuente que fue al lugar (CONAF, Bomberos) confirmo el hecho. */
  | 'confirmed'
  /** Sin confirmación en terreno, pero más de una fuente independiente. */
  | 'corroborated'
  /** Una sola fuente. Tipicamente FIRMS solo, o un reporte ciudadano suelto. */
  | 'single_signal'
  /** Ya no esta activo: controlado, extinguido, sin señales o descartado. */
  | 'closed'

const CLOSED_STATUSES: ReadonlySet<IncidentStatus> = new Set<IncidentStatus>([
  'controlled',
  'extinguished',
  'stale',
  'merged',
  'dismissed',
])

/**
 * Reglas, en orden de prioridad. El estado gana sobre la confianza: un incendio
 * controlado no debe seguir gritando en rojo aunque CONAF lo haya confirmado.
 */
export function phenomenonKey(incident: Incident): PhenomenonKey {
  if (CLOSED_STATUSES.has(incident.status)) return 'closed'
  if (incident.is_official_confirmed) return 'confirmed'
  if (incident.source_count > 1) return 'corroborated'
  return 'single_signal'
}

export interface PhenomenonStyle {
  /** Relleno del marcador y del chip en la UI. */
  color: string
  /** Titulo corto para la leyenda. */
  label: string
  /** Que afirma exactamente este color. Se muestra en la leyenda. */
  meaning: string
  /** Clases Tailwind para los chips fuera del mapa. */
  chip: string
}

export const PHENOMENON: Record<PhenomenonKey, PhenomenonStyle> = {
  confirmed: {
    color: '#dc2626',
    label: 'Confirmado',
    meaning: 'CONAF o Bomberos confirmaron el hecho en terreno.',
    chip: 'bg-red-600 text-white',
  },
  corroborated: {
    color: '#ea580c',
    label: 'En investigación',
    meaning: 'Varias fuentes independientes coinciden, sin confirmación oficial.',
    chip: 'bg-orange-600 text-white',
  },
  single_signal: {
    color: '#eab308',
    label: 'Señal única',
    meaning: 'Una sola fuente. Aún no hay corroboración.',
    chip: 'bg-yellow-400 text-yellow-950',
  },
  closed: {
    color: '#475569',
    label: 'Cerrado',
    meaning: 'Controlado, extinguido o sin señales nuevas.',
    chip: 'bg-slate-600 text-white',
  },
}

// ---------------------------------------------------------------------------
// Eje 2: la alerta oficial
// ---------------------------------------------------------------------------

export interface AlertStyle {
  color: string
  label: string
  chip: string
}

export const ALERT: Record<AlertLevel, AlertStyle> = {
  roja: { color: '#e11d48', label: 'Alerta Roja', chip: 'bg-rose-600 text-white' },
  amarilla: { color: '#f59e0b', label: 'Alerta Amarilla', chip: 'bg-amber-500 text-amber-950' },
  temprana_preventiva: {
    color: '#3b82f6',
    label: 'Alerta Temprana Preventiva',
    chip: 'bg-blue-500 text-white',
  },
  verde: { color: '#22c55e', label: 'Alerta Verde', chip: 'bg-green-500 text-green-950' },
}

export function alertStyle(level: AlertLevel | null): AlertStyle | null {
  return level ? (ALERT[level] ?? null) : null
}

// ---------------------------------------------------------------------------
// Expresiones para MapLibre, derivadas de las tablas de arriba
// ---------------------------------------------------------------------------

/** `['match', ['get','phenomenon'], 'confirmed', '#dc2626', ..., fallback]` */
export const PHENOMENON_COLOR_EXPRESSION = [
  'match',
  ['get', 'phenomenon'],
  ...Object.entries(PHENOMENON).flatMap(([key, style]) => [key, style.color]),
  PHENOMENON.single_signal.color,
] as const

export const ALERT_COLOR_EXPRESSION = [
  'match',
  ['get', 'alert_level'],
  ...Object.entries(ALERT).flatMap(([key, style]) => [key, style.color]),
  'transparent',
] as const
