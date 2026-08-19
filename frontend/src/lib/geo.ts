/**
 * Geometría geodésica sobre la esfera.
 *
 * Las capas nuevas —radio de percepción sísmica y cono de viento— se dibujan
 * como **polígonos en coordenadas reales**, no como círculos de MapLibre.
 *
 * El motivo es que `circle-radius` se mide en píxeles: un círculo de 40 km
 * conservaría su tamaño en pantalla al alejar el zoom y estaría mintiendo sobre
 * la extensión real. Existe el truco de compensar con una interpolación
 * exponencial de base 2 sobre el zoom, pero MapLibre recorta los círculos muy
 * grandes en los bordes de cada tesela, que es justo el caso de un sismo fuerte.
 * Un polígono no tiene ninguno de los dos problemas.
 *
 * Modelo: esfera de radio medio terrestre. El error frente a un elipsoide es
 * del orden del 0,3 %, unos 300 m en un radio de 100 km — despreciable frente a
 * la incertidumbre de las propias estimaciones que se están dibujando.
 */

/** Radio medio terrestre (IUGG), en kilómetros. */
export const EARTH_RADIUS_KM = 6371.0088

const toRad = (deg: number): number => (deg * Math.PI) / 180
const toDeg = (rad: number): number => (rad * 180) / Math.PI

/** `[lon, lat]`, el orden de GeoJSON. */
export type Position = [number, number]

/**
 * Punto de destino: dado un origen, un rumbo y una distancia, sobre el círculo
 * máximo.
 *
 *   φ₂ = asin( sin φ₁ · cos δ + cos φ₁ · sin δ · cos θ )
 *   λ₂ = λ₁ + atan2( sin θ · sin δ · cos φ₁,  cos δ − sin φ₁ · sin φ₂ )
 *
 * donde δ = d / R es la distancia angular y θ el rumbo desde el norte.
 *
 * @param lon  longitud de origen, en grados
 * @param lat  latitud de origen, en grados
 * @param bearingDeg  rumbo desde el norte verdadero, sentido horario
 * @param distanceKm  distancia sobre la superficie
 */
export function destination(
  lon: number,
  lat: number,
  bearingDeg: number,
  distanceKm: number,
): Position {
  const delta = distanceKm / EARTH_RADIUS_KM
  const theta = toRad(bearingDeg)
  const phi1 = toRad(lat)
  const lambda1 = toRad(lon)

  const sinPhi2 =
    Math.sin(phi1) * Math.cos(delta) +
    Math.cos(phi1) * Math.sin(delta) * Math.cos(theta)
  const phi2 = Math.asin(Math.min(1, Math.max(-1, sinPhi2)))

  const lambda2 =
    lambda1 +
    Math.atan2(
      Math.sin(theta) * Math.sin(delta) * Math.cos(phi1),
      Math.cos(delta) - Math.sin(phi1) * sinPhi2,
    )

  // Normalizar a (-180, 180]: cruzar el antimeridiano no pasa en Chile
  // continental, pero un polígono con longitudes fuera de rango se dibuja
  // atravesando el mundo entero y el error es espectacular.
  const lonOut = ((toDeg(lambda2) + 540) % 360) - 180
  return [lonOut, toDeg(phi2)]
}

/**
 * Anillo cerrado que aproxima un círculo geodésico.
 *
 * 72 vértices es un punto de equilibrio: el error de sagita de una cuerda de 5°
 * es 1 − cos(2,5°) ≈ 0,1 % del radio, invisible en pantalla, y el polígono pesa
 * poco aunque haya decenas de sismos.
 */
export function circleRing(
  lon: number,
  lat: number,
  radiusKm: number,
  steps = 72,
): Position[] {
  const ring: Position[] = []
  for (let i = 0; i < steps; i += 1) {
    ring.push(destination(lon, lat, (i * 360) / steps, radiusKm))
  }
  ring.push(ring[0]!) // GeoJSON exige el anillo cerrado
  return ring
}

/**
 * Anillo cerrado de un sector circular (cuña): vértice en el origen, arco a
 * `radiusKm` entre `bearing ± halfAngleDeg`, y vuelta al vértice.
 */
export function sectorRing(
  lon: number,
  lat: number,
  bearingDeg: number,
  halfAngleDeg: number,
  radiusKm: number,
  steps = 24,
): Position[] {
  const ring: Position[] = [[lon, lat]]
  const from = bearingDeg - halfAngleDeg
  const span = halfAngleDeg * 2

  for (let i = 0; i <= steps; i += 1) {
    ring.push(destination(lon, lat, from + (span * i) / steps, radiusKm))
  }

  ring.push([lon, lat])
  return ring
}

/** Acota un valor a un intervalo. */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}
