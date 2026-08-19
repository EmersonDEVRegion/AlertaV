/**
 * Cono de propagación de un incendio según el viento.
 *
 * # Qué es y qué no es
 *
 * Es una **proyección indicativa a una hora**, no un modelo de comportamiento
 * del fuego. Un modelo real (Rothermel, FARSITE) necesita combustible,
 * pendiente, humedad y humedad relativa; acá sólo hay viento. Sirve para
 * responder «¿hacia dónde conviene mirar?», y así se rotula.
 *
 * # La dirección: el error que hay que no cometer
 *
 * Open-Meteo entrega `winddirection` en **convención meteorológica**: el rumbo
 * desde el cual sopla el viento. Un valor de 0° es viento del norte, o sea que
 * el aire —y el fuego— se mueve hacia el **sur**.
 *
 *     rumbo_de_propagación = (winddirection + 180) mod 360
 *
 * Sin esa vuelta de 180° el cono apunta exactamente al revés, señalando la zona
 * segura como si fuera la amenazada. Es el bug más caro posible en esta función
 * y por eso está aislado en `spreadBearing()`, con su prueba.
 *
 * # La longitud
 *
 * Se usa la regla del 10 %, heurística de terreno bien conocida en pastizal
 * abierto: la velocidad de avance del frente ronda el 10 % de la velocidad del
 * viento.
 *
 *     L = 0,10 · v · t
 *
 * con `v` en km/h y `t = 1 h`, de modo que `L` queda en kilómetros. Un viento
 * de 40 km/h proyecta unos 4 km en una hora. Es deliberadamente conservador
 * para pastizal y **subestima** en pendiente ascendente, donde el fuego corre
 * mucho más rápido; la interfaz lo dice.
 *
 * # La apertura
 *
 * El ángulo se estrecha cuando el viento arrecia, que es el comportamiento
 * real: con calma el fuego se expande casi en círculo, y con viento fuerte se
 * alarga en la dirección de avance.
 *
 *     α = clamp(45° − 0,5·v, 12°, 45°)
 *
 * Con viento en calma da 45° a cada lado —una cuña de 90°, casi omnidireccional—
 * y a 60 km/h baja a 15°, un haz marcadamente dirigido.
 */

import { clamp } from '@/lib/geo'

/** Velocidad de avance como fracción de la del viento (regla del 10 %). */
export const SPREAD_RATIO = 0.1

/** Horizonte temporal de la proyección. */
export const PROJECTION_HOURS = 1

/** Longitud mínima dibujable: por debajo, la cuña no se distingue del marcador. */
export const MIN_CONE_KM = 0.8

/** Tope de longitud. Más allá la proyección deja de ser defendible. */
export const MAX_CONE_KM = 25

export interface WindCone {
  /** Rumbo hacia el que avanza el fuego, en grados desde el norte. */
  bearingDeg: number
  /** Semiapertura de la cuña, en grados. */
  halfAngleDeg: number
  /** Alcance proyectado, en kilómetros. */
  lengthKm: number
}

/**
 * Convierte el rumbo meteorológico (de dónde viene) al de propagación (hacia
 * dónde va). Aislada a propósito: es una sola línea y el error que evita es el
 * de apuntar el cono en sentido contrario.
 */
export function spreadBearing(windDirectionDeg: number): number {
  return (windDirectionDeg + 180) % 360
}

/** Longitud proyectada, en km, para una velocidad en km/h. */
export function coneLengthKm(windSpeedKmh: number): number {
  return clamp(
    SPREAD_RATIO * windSpeedKmh * PROJECTION_HOURS,
    MIN_CONE_KM,
    MAX_CONE_KM,
  )
}

/** Semiapertura, en grados, para una velocidad en km/h. */
export function coneHalfAngleDeg(windSpeedKmh: number): number {
  return clamp(45 - 0.5 * windSpeedKmh, 12, 45)
}

/**
 * Geometría de la cuña. `null` si los datos no permiten dibujarla: sin viento
 * medible no hay dirección de propagación que afirmar.
 */
export function windConeFor(
  windSpeedKmh: number | null | undefined,
  windDirectionDeg: number | null | undefined,
): WindCone | null {
  if (
    typeof windSpeedKmh !== 'number' ||
    typeof windDirectionDeg !== 'number' ||
    !Number.isFinite(windSpeedKmh) ||
    !Number.isFinite(windDirectionDeg) ||
    windSpeedKmh <= 0
  ) {
    return null
  }

  return {
    bearingDeg: spreadBearing(windDirectionDeg),
    halfAngleDeg: coneHalfAngleDeg(windSpeedKmh),
    lengthKm: coneLengthKm(windSpeedKmh),
  }
}

/** Rosa de 8 rumbos, para rotular la dirección en la ficha. */
const COMPASS = [
  'norte', 'noreste', 'este', 'sureste',
  'sur', 'suroeste', 'oeste', 'noroeste',
] as const

export function compassLabel(bearingDeg: number): string {
  const index = Math.round(((bearingDeg % 360) + 360) % 360 / 45) % 8
  return COMPASS[index]!
}
