/**
 * Cliente del feed de prensa: `GET /api/v1/feed/noticias`.
 *
 * Ruta RELATIVA a `env.apiBaseUrl` (que ya termina en `/api/v1`), como el
 * resto. Se valida la respuesta por lo mismo que el radar de vehículos: una
 * nota mal formada no puede tumbar la lista entera.
 */

import { apiGet, buildQuery } from './client'
import type { HealthStatus } from './health'
import type { NewsFeedItem, NewsFeedQuery, NewsFeedResponse, NewsFeedSource } from './newsFeedTypes'

const HEALTH_STATUSES: readonly HealthStatus[] = ['ok', 'degraded', 'failing', 'stale', 'never']

type Raw = Record<string, unknown>

function isRecord(value: unknown): value is Raw {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : null
}

/** Sólo enlaces http(s): lo que viene de un portal ajeno no se pinta como `href` sin mirar. */
function safeUrl(value: unknown): string | null {
  const url = text(value)
  return url && /^https?:\/\//i.test(url) ? url : null
}

function parseItem(raw: unknown): NewsFeedItem | null {
  if (!isRecord(raw)) return null
  const id = text(raw.id)
  const titular = text(raw.titular)
  const publicada = text(raw.publicada_en)
  if (!id || !titular || !publicada || !Number.isFinite(Date.parse(publicada))) return null
  return {
    id,
    titular,
    bajada: text(raw.bajada),
    medio: text(raw.medio),
    url: safeUrl(raw.url),
    comuna: text(raw.comuna),
    tipo: text(raw.tipo) ?? 'other',
    publicada_en: publicada,
    hora_aproximada: raw.hora_aproximada === true,
    detectada_en: text(raw.detectada_en) ?? publicada,
  }
}

function parseSource(raw: unknown): NewsFeedSource {
  const source = isRecord(raw) ? raw : {}
  const estado = source.estado
  return {
    collector: text(source.collector) ?? 'prensa_local',
    estado:
      typeof estado === 'string' && (HEALTH_STATUSES as readonly string[]).includes(estado)
        ? (estado as HealthStatus)
        : undefined,
    ultima_corrida: text(source.ultima_corrida),
    detalle: text(source.detalle),
  }
}

export function parseNewsFeed(payload: unknown): NewsFeedResponse {
  const body = isRecord(payload) ? payload : {}
  const rawItems = Array.isArray(body.items) ? body.items : []
  const items = rawItems.map(parseItem).filter((item): item is NewsFeedItem => item !== null)
  return {
    generado_en: text(body.generado_en) ?? '',
    horas: typeof body.horas === 'number' ? body.horas : 0,
    total: items.length,
    items,
    fuente: parseSource(body.fuente),
  }
}

export async function fetchNewsFeed(
  params: NewsFeedQuery = {},
  signal?: AbortSignal,
): Promise<NewsFeedResponse> {
  const query = buildQuery({ horas: params.horas, limit: params.limit })
  const payload = await apiGet<unknown>(`/feed/noticias${query}`, signal)
  return parseNewsFeed(payload)
}
