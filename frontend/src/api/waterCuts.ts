/**
 * Cliente de la capa de cortes de agua: `GET /api/v1/events/water-cuts/geojson`.
 *
 * La ruta va RELATIVA a `env.apiBaseUrl`, que ya termina en `/api/v1`. Ver la
 * nota de `roadClosures.ts`: escribirla absoluta producía `/api/v1/api/v1/…`.
 *
 * # Por qué se valida la respuesta
 *
 * Lo mismo que en el radar: un corte mal formado no puede tumbar la capa. Se
 * descarta y se avisa **una** vez por consola. Sin `public_id` no hay forma de
 * seleccionarlo ni de mantenerlo estable entre sondeos, así que ése es el único
 * campo obligatorio; el resto se muestra si llega.
 */

import { apiGet } from './client'
import type { HealthStatus } from './health'
import type { WaterCut, WaterCutSource, WaterCutsResponse } from './waterCutTypes'

const HEALTH_STATUSES: readonly HealthStatus[] = ['ok', 'degraded', 'failing', 'stale', 'never']

let warnedAboutCuts = false

type Raw = Record<string, unknown>

function isRecord(value: unknown): value is Raw {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : null
}

function bool(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

const ISO_DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/

/**
 * Una fecha que no se deja leer es tan inútil como una ausente. Se exige la
 * forma ISO y no sólo `Date.parse`: V8 acepta textos como «mañana a las 5» y
 * les inventa una fecha.
 */
function isoDate(value: unknown): string | null {
  const raw = text(value)
  return raw !== null && ISO_DATE_TIME.test(raw) && Number.isFinite(Date.parse(raw)) ? raw : null
}

/** Sólo enlaces https: lo que llegue distinto no se publica como enlace. */
function httpsUrl(value: unknown): string | null {
  const raw = text(value)
  return raw !== null && raw.startsWith('https://') ? raw : null
}

function point(geometry: unknown): [number, number] | null {
  if (!isRecord(geometry) || geometry.type !== 'Point') return null
  const coords = geometry.coordinates
  if (!Array.isArray(coords) || coords.length < 2) return null
  const [lon, lat] = coords
  return typeof lon === 'number' &&
    typeof lat === 'number' &&
    Number.isFinite(lon) &&
    Number.isFinite(lat) &&
    Math.abs(lat) <= 90 &&
    Math.abs(lon) <= 180
    ? [lon, lat]
    : null
}

function parseCut(raw: unknown): WaterCut | null {
  if (!isRecord(raw) || !isRecord(raw.properties)) return null
  const props = raw.properties
  const id = text(props.public_id)
  if (!id) return null

  return {
    id,
    sisda: text(props.sisda),
    comuna: text(props.comuna),
    tipo: text(props.tipo),
    programado: bool(props.programado),
    motivo: text(props.motivo),
    calles: text(props.calles),
    sector: text(props.sector),
    inicio: isoDate(props.inicio),
    fin: isoDate(props.fin),
    suministro_alternativo: bool(props.suministro_alternativo),
    url_mapa: httpsUrl(props.url_mapa),
    visto_en: isoDate(props.visto_en),
    coordinates: point(raw.geometry),
  }
}

function parseSource(raw: unknown): WaterCutSource {
  const source = isRecord(raw) ? raw : {}
  const estado = text(source.estado)
  return {
    collector: text(source.collector) ?? 'esval_cortes_agua',
    estado: HEALTH_STATUSES.find((status) => status === estado),
    ultima_corrida: isoDate(source.ultima_corrida),
    ultima_lectura: isoDate(source.ultima_lectura),
    detalle: text(source.detalle),
  }
}

export function parseWaterCuts(payload: unknown): WaterCutsResponse {
  const body = isRecord(payload) ? payload : {}
  const features = Array.isArray(body.features) ? body.features : []

  const cuts: WaterCut[] = []
  let dropped = 0
  for (const raw of features) {
    const cut = parseCut(raw)
    if (cut) cuts.push(cut)
    else dropped += 1
  }

  if (dropped > 0 && !warnedAboutCuts) {
    warnedAboutCuts = true
    console.warn(
      `[AlertaV/agua] Se descartaron ${dropped} corte(s) con forma inesperada. ` +
        'Revisa `feature_de_corte` en backend/app/services/water_cut_service.py: ' +
        'este cliente exige `properties.public_id`.',
    )
  }

  return {
    generado_en: text(body.generado_en) ?? '',
    cuts,
    fuente: parseSource(body.fuente),
  }
}

export async function fetchWaterCuts(signal?: AbortSignal): Promise<WaterCutsResponse> {
  const payload = await apiGet<unknown>('/events/water-cuts/geojson', signal)
  return parseWaterCuts(payload)
}

/** Para los tests: el aviso de consola es uno por sesión. */
export function resetWaterCutWarnings(): void {
  warnedAboutCuts = false
}
