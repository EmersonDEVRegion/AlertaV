/**
 * Cliente de Open-Meteo para el viento actual.
 *
 * Es la única llamada de la PWA a un tercero: no pasa por el backend de AlertaV
 * ni por su cliente `apiGet`, porque tiene otro origen, otro contrato de
 * errores y ninguna relación con `/api/v1`.
 *
 * Open-Meteo es gratuita y sin clave para uso no comercial. Al ser un servicio
 * externo se asume que puede fallar o cambiar: la respuesta se valida campo por
 * campo antes de usarse, y si algo no calza se devuelve `null` en vez de dejar
 * que un `undefined` llegue a la trigonometría y produzca un cono en NaN que
 * MapLibre dibujaría en cualquier parte.
 */

/** Lo único que se consume de `current_weather`. */
export interface CurrentWind {
  /** Velocidad en km/h (unidad por defecto de Open-Meteo). */
  windSpeedKmh: number
  /** Rumbo METEOROLÓGICO: desde dónde sopla. Ver `domain/windCone.ts`. */
  windDirectionDeg: number
  /** Marca temporal de la observación, según el servicio. */
  observedAt: string | null
}

const ENDPOINT = 'https://api.open-meteo.com/v1/forecast'

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/**
 * Extrae el viento de una respuesta cruda.
 *
 * Exportada para poder probarla sin red: es donde vive todo el riesgo de que el
 * servicio devuelva algo distinto de lo esperado.
 */
export function parseCurrentWind(payload: unknown): CurrentWind | null {
  if (typeof payload !== 'object' || payload === null) return null

  const current = (payload as Record<string, unknown>)['current_weather']
  if (typeof current !== 'object' || current === null) return null

  const record = current as Record<string, unknown>
  const speed = record['windspeed']
  const direction = record['winddirection']

  if (!isFiniteNumber(speed) || !isFiniteNumber(direction)) return null
  // Una dirección fuera de rango delata un cambio de contrato; mejor no dibujar
  // que dibujar hacia un rumbo inventado.
  if (direction < 0 || direction > 360) return null
  if (speed < 0) return null

  const time = record['time']
  return {
    windSpeedKmh: speed,
    windDirectionDeg: direction,
    observedAt: typeof time === 'string' ? time : null,
  }
}

export async function fetchCurrentWind(
  lat: number,
  lon: number,
  signal?: AbortSignal,
): Promise<CurrentWind | null> {
  const url =
    `${ENDPOINT}?latitude=${lat.toFixed(4)}&longitude=${lon.toFixed(4)}` +
    `&current_weather=true`

  const response = await fetch(url, {
    signal: signal ?? null,
    headers: { Accept: 'application/json' },
  })

  if (!response.ok) {
    throw new Error(`Open-Meteo respondió ${response.status}`)
  }

  return parseCurrentWind(await response.json())
}
