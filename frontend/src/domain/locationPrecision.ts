/**
 * Qué tan lejos puede estar el hecho del punto que se dibuja.
 *
 * La central de Bomberos escribe la esquina más cercana, y lo demás llega
 * menos fino. El radio de cada precisión sale de lo medido en octubre de 2026:
 *
 * - `intersection`: media cuadra. El siniestro está en alguna de las cuatro
 *   esquinas de la cuadra nombrada.
 * - `street`: el punto quedó sobre una de las calles, no en la esquina. Entre
 *   30 y 314 m de la esquina en ciudad, y con el índice de esquinas sólo quedan
 *   así los despachos que no nombran un cruce.
 * - `sector`: el tamaño típico de un barrio o población.
 * - `despacho`, `exacta` y `address`: el marcador de la imagen de Viper (el
 *   punto con que despachó la central), coordenadas propias de la fuente o una
 *   dirección con número. No llevan círculo.
 */

import type { UbicacionPrecision } from '@/api/types'

export const INCERTIDUMBRE_M: Record<UbicacionPrecision, number> = {
  despacho: 0,
  exacta: 0,
  address: 0,
  intersection: 60,
  street: 200,
  sector: 600,
}

/** Radio en metros del círculo de un incidente. 0 = sin círculo. */
export function incertidumbreDe(precision: UbicacionPrecision | null | undefined): number {
  return precision ? (INCERTIDUMBRE_M[precision] ?? 0) : 0
}
