/**
 * Simbología del mapa — política de confianza v2.0.0.
 *
 * El color del pin lo decide `confidence_level`, el tramo de tres estados que
 * calcula el motor de correlación. Los tres colores y los tres cortes son los
 * que declara el backend en `app/models/enums.py` (`LEVEL_STYLES`,
 * `UNSAFE_THRESHOLD`, `CONFIRMED_THRESHOLD`); acá sólo se replican.
 *
 *   unsafe    < 30 %        rojo    #dc2626   señal aislada o ruido
 *   possible  30 % – 60 %   amarillo #eab308  hay algo, no sabemos qué
 *   confirmed > 60 %        naranja #ea580c   evidencia acumulada
 *
 * El rojo de `unsafe` es de **advertencia sobre el dato**, no de emergencia:
 * dice "no te fíes de esto todavía". Es contraintuitivo respecto de la
 * convención habitual de mapas, así que la leyenda lo explica con todas las
 * letras en vez de confiar en que el color se entienda solo.
 *
 * Sobre esa escala se montan dos cosas que NO son color, para no gastar dos
 * veces el mismo canal visual:
 *
 *   - **Estado**: los incidentes cerrados llevan el mismo color atenuado y
 *     anillo punteado. Un incendio extinguido no puede verse igual que uno
 *     ardiendo.
 *   - **Verificación institucional**: `confidence_level = 'confirmed'` NO
 *     significa que alguien fue al lugar. Los que no tienen
 *     `is_official_confirmed` llevan un punto central hueco.
 *
 * Y el anillo exterior sigue siendo el eje de SENAPRED, independiente de todo
 * lo anterior.
 */

import type { AlertLevel, ConfidenceLevel, Incident, IncidentStatus } from '@/api/types'
import { CONFIRMED_THRESHOLD, UNSAFE_THRESHOLD } from '@/api/types'

// ---------------------------------------------------------------------------
// Eje 1: el tramo de confianza
// ---------------------------------------------------------------------------

export interface LevelStyle {
  /** Hex exacto declarado por el backend. */
  color: string
  /** Etiqueta corta, la misma que usa `LEVEL_STYLES`. */
  label: string
  /** Qué afirma este tramo. Se muestra en la leyenda, no en un tooltip. */
  meaning: string
  /** Rango legible, para la leyenda. */
  range: string
  /** Clases Tailwind para los chips fuera del mapa. */
  chip: string
}

export const LEVEL: Record<ConfidenceLevel, LevelStyle> = {
  unsafe: {
    color: '#dc2626',
    label: 'Baja confianza',
    range: 'menos de 30 %',
    meaning: 'Señal aislada sin corroborar. Puede ser ruido o spam.',
    chip: 'bg-red-600 text-white',
  },
  possible: {
    color: '#eab308',
    label: 'Posible emergencia',
    range: '30 % a 60 %',
    meaning: 'Hay evidencia, no alcanza para afirmar que hay fuego.',
    chip: 'bg-yellow-400 text-yellow-950',
  },
  confirmed: {
    color: '#ea580c',
    label: 'Incendio confirmado',
    range: 'más de 60 %',
    meaning: 'Evidencia acumulada por sobre el 60 %.',
    chip: 'bg-orange-600 text-white',
  },
}

/** Orden de presentación en la leyenda: de menor a mayor evidencia. */
export const LEVEL_ORDER: readonly ConfidenceLevel[] = ['unsafe', 'possible', 'confirmed']

/**
 * Réplica exacta de `level_for()` del backend, incluidos los bordes: 0.30 ya es
 * `possible` y 0.60 exacto **todavía** lo es; sólo se cruza a `confirmed` por
 * encima de 0.60.
 *
 * Es un camino de respaldo, no la vía normal. El backend manda `confidence_level`
 * en cada respuesta; esto sólo actúa si falta, que en la práctica significa una
 * respuesta antigua servida desde la caché del service worker.
 */
export function levelFor(confidence: number): ConfidenceLevel {
  if (confidence < UNSAFE_THRESHOLD) return 'unsafe'
  if (confidence > CONFIRMED_THRESHOLD) return 'confirmed'
  return 'possible'
}

/** Tramo de un incidente: el del backend si viene, recalculado si no. */
export function levelOf(incident: Incident): ConfidenceLevel {
  return incident.confidence_level ?? levelFor(incident.confidence)
}

// ---------------------------------------------------------------------------
// Eje 2: el estado (textura, no color)
// ---------------------------------------------------------------------------

const CLOSED_STATUSES: ReadonlySet<IncidentStatus> = new Set<IncidentStatus>([
  'controlled',
  'extinguished',
  'stale',
  'merged',
  'dismissed',
])

export function isClosed(status: IncidentStatus): boolean {
  return CLOSED_STATUSES.has(status)
}

/**
 * Versión apagada de un color, mezclándolo contra el gris de pizarra.
 *
 * Se calcula en vez de escribirse a mano para que la paleta atenuada no pueda
 * separarse de la paleta base: si el backend cambia un hex, el atenuado lo
 * sigue solo.
 */
export function mute(hex: string, amount = 0.55): string {
  const SLATE = [100, 116, 139] as const
  const value = hex.replace('#', '')
  const channels = [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16))
  return (
    '#' +
    channels
      .map((c, i) =>
        Math.round(c * (1 - amount) + SLATE[i]! * amount)
          .toString(16)
          .padStart(2, '0'),
      )
      .join('')
  )
}

export const MUTED_LEVEL: Record<ConfidenceLevel, string> = {
  unsafe: mute(LEVEL.unsafe.color),
  possible: mute(LEVEL.possible.color),
  confirmed: mute(LEVEL.confirmed.color),
}

// ---------------------------------------------------------------------------
// Eje 3: verificación institucional (marca, no color)
// ---------------------------------------------------------------------------

/**
 * ¿El tramo dice `confirmed` sin que nadie haya ido al lugar?
 *
 * Es el caso que el backend advierte explícitamente: un racimo de despachos
 * radiales cruza el 60 % y queda `confirmed` con `is_official_confirmed = false`.
 * Pintarlo naranja y rotularlo "Incendio confirmado" sin más sería atribuirle a
 * CONAF una confirmación que nunca hizo.
 */
export function needsVerificationCaveat(incident: Incident): boolean {
  return levelOf(incident) === 'confirmed' && !incident.is_official_confirmed
}

// ---------------------------------------------------------------------------
// Eje 4: la alerta de SENAPRED (independiente de todo lo anterior)
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
// Expresiones para MapLibre
// ---------------------------------------------------------------------------

// El color del relleno dejó de depender sólo del tramo: desde que hay capas de
// tráfico y de otras emergencias, la paleta la decide primero la familia. Esa
// expresión vive en `domain/palette.ts`, que es quien conoce las tres tablas.
// Acá queda sólo el eje de SENAPRED, que es transversal a todas las familias.

export const ALERT_COLOR_EXPRESSION = [
  'match',
  ['get', 'alert_level'],
  ...Object.entries(ALERT).flatMap(([key, style]) => [key, style.color]),
  'transparent',
] as const
