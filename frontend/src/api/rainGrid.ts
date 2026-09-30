/**
 * Grilla de lluvia para el mapa de calor. Espejo de `RainGridRead` del backend
 * (`/events/weather/grid`).
 *
 * Parseo tolerante, como el resto de `api/`: una respuesta rara no rompe el
 * mapa, deja la capa sin campo y lo avisa una vez en la consola.
 */

import { apiGet } from './client'

export interface RainGrid {
  /** Cuándo se leyó de Open-Meteo, en ms. */
  generatedAt: number
  /** Horas hacia adelante sobre las que se tomó el máximo. */
  hours: number
  step: number
  /** Longitud de la columna 0 y latitud de la fila 0 (la del norte). */
  west: number
  north: number
  nx: number
  ny: number
  /** `nx × ny` valores en mm/h, fila a fila de norte a sur. `null` = sin dato. */
  values: readonly (number | null)[]
}

type RainGridState = 'ok' | 'stale' | 'failing' | 'never'

interface RainGridResponse {
  grid: RainGrid | null
  state: RainGridState
}

const STATES: readonly RainGridState[] = ['ok', 'stale', 'failing', 'never']

const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

export function parseRainGrid(payload: unknown): RainGridResponse {
  const body = (payload ?? {}) as Record<string, unknown>
  const source = (body.fuente ?? {}) as Record<string, unknown>
  const state = STATES.includes(source.estado as RainGridState)
    ? (source.estado as RainGridState)
    : 'never'

  const raw = body.grilla as Record<string, unknown> | null | undefined
  if (!raw) return { grid: null, state }

  const nx = num(raw.nx)
  const ny = num(raw.ny)
  const step = num(raw.paso)
  const west = num(raw.oeste)
  const north = num(raw.norte)
  const generatedAt = Date.parse(String(raw.generado_en))
  const values = Array.isArray(raw.valores) ? raw.valores.map(num) : null

  if (
    nx === null ||
    ny === null ||
    nx < 2 ||
    ny < 2 ||
    step === null ||
    step <= 0 ||
    west === null ||
    north === null ||
    Number.isNaN(generatedAt) ||
    values === null ||
    values.length !== nx * ny
  ) {
    console.warn('[AlertaV/lluvia] grilla con forma inesperada; se omite', {
      nx,
      ny,
      valores: Array.isArray(raw.valores) ? raw.valores.length : typeof raw.valores,
    })
    return { grid: null, state }
  }

  return {
    grid: { generatedAt, hours: num(raw.horas) ?? 24, step, west, north, nx, ny, values },
    state,
  }
}

export async function fetchRainGrid(signal?: AbortSignal): Promise<RainGridResponse> {
  return parseRainGrid(await apiGet<unknown>('/events/weather/grid', signal))
}
