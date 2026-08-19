/**
 * Radio estimado de percepción de un sismo.
 *
 * # Qué es y qué no es
 *
 * Es una **estimación indicativa** de hasta dónde el sismo pudo sentirse, no un
 * mapa de intensidades. El USGS publica mapas ShakeMap reales que incorporan
 * geología local, tipo de suelo y directividad de la ruptura; nada de eso está
 * acá. Sirve para dar escala —«esto se sintió en toda la región» contra «esto
 * casi nadie lo notó»— y así se rotula en la interfaz.
 *
 * # La fórmula
 *
 * Se parte de una relación de atenuación de intensidad de forma clásica, la
 * misma familia que usan Bakun–Wentworth o Grünthal:
 *
 *     I(D) = a + b·M − c·log₁₀(D)
 *
 * donde `I` es intensidad tipo Mercalli, `M` la magnitud y `D` la distancia
 * **hipocentral** en km. Con los coeficientes de corteza somera que se usan acá
 * (a = 1,7; b = 1,5; c = 3,0), un M5 a 10 km da I ≈ 6,2 y a 100 km da I ≈ 3,2,
 * que es el orden correcto.
 *
 * Se despeja la distancia a la que la intensidad cae al umbral de percepción:
 *
 *     D_hipo = 10^((a + b·M − I_min) / c)
 *
 * `I_min = 2,5` corresponde a «perceptible por algunas personas en reposo»,
 * entre los grados II y III de Mercalli.
 *
 * # De distancia hipocentral a radio en superficie
 *
 * `D_hipo` se mide desde el foco, que está a `h` kilómetros bajo tierra. El
 * radio que hay que dibujar en el mapa es el cateto horizontal del triángulo
 * rectángulo:
 *
 *     R_superficie = √( D_hipo² − h² )
 *
 * Es la parte que hace que `depth_km` importe de verdad y no sea decoración: a
 * igual magnitud, un sismo profundo se siente en un área **menor**, porque
 * buena parte de la distancia al observador ya se gastó en la vertical. Y si
 * `h ≥ D_hipo` el radicando es negativo, lo que significa que ni siquiera en el
 * epicentro se alcanza el umbral: no se dibuja nada, que es la respuesta
 * correcta para un microsismo profundo.
 */

import type { SeismicEvent } from '@/api/seismicTypes'
import { clamp } from '@/lib/geo'

/** Coeficientes de la relación de atenuación. */
export const ATTENUATION = { a: 1.7, b: 1.5, c: 3.0 } as const

/** Umbral de percepción, en grados de Mercalli. */
export const PERCEPTION_INTENSITY = 2.5

/**
 * Profundidad supuesta cuando el USGS no la entrega. 15 km es un valor central
 * para sismos corticales; se supone en vez de omitirse porque tratar la
 * profundidad como 0 exageraría el radio justo en el caso peor informado.
 */
export const ASSUMED_DEPTH_KM = 15

/**
 * Tope del radio dibujable. Un M7 sale por sobre los 1.000 km con esta fórmula
 * —y sí, un M7 se siente lejísimos—, pero un círculo así deja de informar y
 * pasa a tapar el mapa entero.
 */
export const MAX_REACH_KM = 400

/** Bajo este radio el círculo es más chico que el propio marcador. */
export const MIN_DRAWABLE_KM = 1.5

/** Distancia hipocentral, en km, a la que se alcanza el umbral de percepción. */
export function hypocentralReachKm(magnitude: number): number {
  const { a, b, c } = ATTENUATION
  return 10 ** ((a + b * magnitude - PERCEPTION_INTENSITY) / c)
}

/**
 * Radio en superficie, en km. `null` cuando no se puede afirmar nada:
 *
 *   - sin magnitud (solución preliminar del USGS);
 *   - o el foco está más lejos que el alcance, o sea no se siente en ninguna
 *     parte.
 */
export function perceptionRadiusKm(event: SeismicEvent): number | null {
  if (event.magnitude === null || Number.isNaN(event.magnitude)) return null

  // La profundidad puede venir negativa (se mide desde el nivel del mar); lo que
  // importa geométricamente es la separación vertical.
  const depth = Math.abs(event.depth_km ?? ASSUMED_DEPTH_KM)
  const hypocentral = hypocentralReachKm(event.magnitude)

  const squared = hypocentral ** 2 - depth ** 2
  if (squared <= 0) return null

  const radius = Math.sqrt(squared)
  return radius < MIN_DRAWABLE_KM ? null : clamp(radius, 0, MAX_REACH_KM)
}

/** ¿El radio quedó recortado por el tope? La ficha lo advierte. */
export function isReachClamped(event: SeismicEvent): boolean {
  const raw = perceptionRadiusKm(event)
  return raw !== null && raw >= MAX_REACH_KM
}
