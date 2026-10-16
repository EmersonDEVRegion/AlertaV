/**
 * Cliente del feed de prensa: `GET /api/v1/feed/noticias`.
 *
 * Ruta RELATIVA a `env.apiBaseUrl` (que ya termina en `/api/v1`), como el
 * resto. Se valida la respuesta por lo mismo que el radar de vehículos: una
 * nota mal formada no puede tumbar la lista entera.
 */

import { apiGet, buildQuery } from './client'
import { isRecord, trimmedText } from './parse'
import type { HealthStatus } from './health'
import type { NewsFeedItem, NewsFeedQuery, NewsFeedResponse, NewsFeedSource } from './newsFeedTypes'

const HEALTH_STATUSES: readonly HealthStatus[] = ['ok', 'degraded', 'failing', 'stale', 'never']

/** Sólo enlaces http(s): lo que viene de un portal ajeno no se pinta como `href` sin mirar. */
function safeUrl(value: unknown): string | null {
  const url = trimmedText(value)
  return url && /^https?:\/\//i.test(url) ? url : null
}

function parseItem(raw: unknown): NewsFeedItem | null {
  if (!isRecord(raw)) return null
  const id = trimmedText(raw.id)
  const titular = trimmedText(raw.titular)
  const publicada = trimmedText(raw.publicada_en)
  if (!id || !titular || !publicada || !Number.isFinite(Date.parse(publicada))) return null
  return {
    id,
    titular,
    bajada: trimmedText(raw.bajada),
    medio: trimmedText(raw.medio),
    url: safeUrl(raw.url),
    comuna: trimmedText(raw.comuna),
    tipo: trimmedText(raw.tipo) ?? 'other',
    publicada_en: publicada,
    hora_aproximada: raw.hora_aproximada === true,
    detectada_en: trimmedText(raw.detectada_en) ?? publicada,
  }
}

function parseSource(raw: unknown): NewsFeedSource {
  const source = isRecord(raw) ? raw : {}
  const estado = source.estado
  return {
    collector: trimmedText(source.collector) ?? 'prensa_local',
    estado:
      typeof estado === 'string' && (HEALTH_STATUSES as readonly string[]).includes(estado)
        ? (estado as HealthStatus)
        : undefined,
    ultima_corrida: trimmedText(source.ultima_corrida),
    detalle: trimmedText(source.detalle),
  }
}

export function parseNewsFeed(payload: unknown): NewsFeedResponse {
  const body = isRecord(payload) ? payload : {}
  const rawItems = Array.isArray(body.items) ? body.items : []
  const items = rawItems.map(parseItem).filter((item): item is NewsFeedItem => item !== null)
  return {
    generado_en: trimmedText(body.generado_en) ?? '',
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
