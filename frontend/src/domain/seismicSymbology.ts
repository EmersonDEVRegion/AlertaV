/**
 * Simbología de la capa sísmica.
 *
 * **Módulo deliberadamente separado de `domain/symbology.ts`.** No comparte
 * paleta, ni escala, ni vocabulario con los incendios, y no debe hacerlo:
 *
 *   - Los incendios se colorean por `confidence_level`, que mide *cuánta
 *     evidencia hay de que el hecho exista*. Un sismo registrado por una red
 *     sismológica no tiene esa duda: es un hecho medido.
 *   - Acá el color codifica **magnitud**, que es intensidad física.
 *
 * Que ambas escalas terminen usando tonos cálidos es una coincidencia
 * desafortunada, no un vínculo. Se compensa con la forma: los sismos se dibujan
 * como círculos huecos de trazo grueso, los incendios como discos sólidos. En
 * el mapa se distinguen sin leer la leyenda.
 *
 * Tampoco hay `is_official_confirmed` ni estado que atenuar: un sismo ocurrió o
 * no ocurrió.
 */

import type { SeismicEvent } from '@/api/seismicTypes'

// ---------------------------------------------------------------------------
// Escala por magnitud
// ---------------------------------------------------------------------------

export type MagnitudeBand = 'menor' | 'moderado' | 'fuerte' | 'desconocido'

/** Cortes de la escala. Los pidió el producto: 4.0 y 5.5. */
export const MODERATE_THRESHOLD = 4.0
export const STRONG_THRESHOLD = 5.5

export interface MagnitudeStyle {
  color: string
  label: string
  range: string
  meaning: string
  chip: string
}

export const MAGNITUDE: Record<MagnitudeBand, MagnitudeStyle> = {
  menor: {
    color: '#facc15',
    label: 'Menor',
    range: 'M < 4,0',
    meaning: 'Perceptible cerca del epicentro. Rara vez causa daños.',
    chip: 'bg-yellow-300 text-yellow-950',
  },
  moderado: {
    color: '#f97316',
    label: 'Moderado',
    range: 'M 4,0 – 5,5',
    meaning: 'Se siente con claridad. Puede dañar construcciones vulnerables.',
    chip: 'bg-orange-500 text-white',
  },
  fuerte: {
    color: '#991b1b',
    label: 'Fuerte',
    range: 'M > 5,5',
    meaning: 'Daño potencial en un área amplia.',
    chip: 'bg-red-900 text-white',
  },
  /**
   * El USGS publica la detección antes de terminar de calcular la magnitud.
   * Un sismo sin magnitud existe y hay que dibujarlo; pintarlo en la banda baja
   * afirmaría que fue menor, que es justo lo que todavía no se sabe.
   */
  desconocido: {
    color: '#64748b',
    label: 'Sin magnitud',
    range: 'preliminar',
    meaning: 'Solución preliminar del USGS: la magnitud aún no está calculada.',
    chip: 'bg-slate-500 text-white',
  },
}

export const MAGNITUDE_ORDER: readonly MagnitudeBand[] = [
  'menor',
  'moderado',
  'fuerte',
  'desconocido',
]

/** Banda de una magnitud. `null` (solución preliminar) tiene su propia banda. */
export function magnitudeBand(magnitude: number | null): MagnitudeBand {
  if (magnitude === null || Number.isNaN(magnitude)) return 'desconocido'
  if (magnitude > STRONG_THRESHOLD) return 'fuerte'
  if (magnitude >= MODERATE_THRESHOLD) return 'moderado'
  return 'menor'
}

export function bandOf(event: SeismicEvent): MagnitudeBand {
  return magnitudeBand(event.magnitude)
}

// ---------------------------------------------------------------------------
// Radio
// ---------------------------------------------------------------------------

/**
 * Magnitud usada para dimensionar el círculo, acotada al rango dibujable.
 *
 * La escala de magnitud es logarítmica: un M6 libera unas mil veces más energía
 * que un M4. Reproducir eso en el radio daría un punto invisible y una mancha
 * que taparía media región, así que el radio crece de forma perceptual —lineal
 * sobre la magnitud— entre M2 y M7. Es una decisión de legibilidad, y por eso
 * la leyenda muestra los tamaños en vez de dejarlos a interpretación.
 *
 * Un sismo sin magnitud se dibuja en el tamaño mínimo: no puede reclamar
 * presencia visual que su dato no respalda.
 */
export const MIN_SIZED_MAGNITUDE = 2.0
export const MAX_SIZED_MAGNITUDE = 7.0

export function sizingMagnitude(magnitude: number | null): number {
  if (magnitude === null || Number.isNaN(magnitude)) return MIN_SIZED_MAGNITUDE
  return Math.min(MAX_SIZED_MAGNITUDE, Math.max(MIN_SIZED_MAGNITUDE, magnitude))
}

/** Radio en píxeles a un zoom de referencia. Sólo para la leyenda. */
export function legendRadius(magnitude: number | null): number {
  const m = sizingMagnitude(magnitude)
  return 4 + ((m - MIN_SIZED_MAGNITUDE) / (MAX_SIZED_MAGNITUDE - MIN_SIZED_MAGNITUDE)) * 14
}

// ---------------------------------------------------------------------------
// Expresiones para MapLibre
// ---------------------------------------------------------------------------

/** `['match', ['get','band'], 'menor', '#facc15', …]` */
export const MAGNITUDE_COLOR_EXPRESSION = [
  'match',
  ['get', 'band'],
  ...MAGNITUDE_ORDER.flatMap((key) => [key, MAGNITUDE[key].color]),
  MAGNITUDE.desconocido.color,
] as const
