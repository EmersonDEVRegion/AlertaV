/**
 * Cliente de los avisos de la DMC: `GET /api/v1/feed/avisos-meteo`.
 *
 * Se valida la respuesta por lo mismo que el feed de noticias: un aviso mal
 * formado no puede tumbar la franja entera. El enlace oficial sólo se acepta
 * si apunta a la DMC.
 */

import { apiGet } from './client'
import { isRecord, trimmedText } from './parse'
import type { HealthStatus } from './health'
import {
  METEO_WARNING_TYPES,
  type MeteoWarning,
  type MeteoWarningResponse,
  type MeteoWarningSource,
  type MeteoWarningTable,
  type MeteoWarningType,
  type MeteoWarningZone,
} from './meteoWarningTypes'

const HEALTH_STATUSES: readonly HealthStatus[] = ['ok', 'degraded', 'failing', 'stale', 'never']
const DMC_FALLBACK_URL = 'https://archivos.meteochile.gob.cl/portaldmc/AAA/aaa_mapa.php'

function dmcUrl(value: unknown): string {
  const url = trimmedText(value)
  if (!url) return DMC_FALLBACK_URL
  try {
    const parsed = new URL(url)
    const host = parsed.hostname.toLowerCase()
    return parsed.protocol === 'https:' &&
      (host === 'meteochile.gob.cl' || host.endsWith('.meteochile.gob.cl'))
      ? parsed.href
      : DMC_FALLBACK_URL
  } catch {
    return DMC_FALLBACK_URL
  }
}

function isoOrNull(value: unknown): string | null {
  const text = trimmedText(value)
  return text && Number.isFinite(Date.parse(text)) ? text : null
}

function texts(value: unknown): string[] {
  return Array.isArray(value) ? value.map((v) => trimmedText(v) ?? '') : []
}

function parseTable(raw: unknown): MeteoWarningTable | null {
  if (!isRecord(raw) || !Array.isArray(raw.filas)) return null
  const filas = raw.filas
    .filter(isRecord)
    .map((f) => ({ zona: trimmedText(f.zona) ?? '', valores: texts(f.valores) }))
    .filter((f) => f.zona !== '')
  if (filas.length === 0) return null
  return { titulo: trimmedText(raw.titulo), columnas: texts(raw.columnas), filas }
}

function parseZones(raw: unknown): MeteoWarningZone[] {
  if (!Array.isArray(raw)) return []
  return raw.filter(isRecord).flatMap((z) => {
    const clave = trimmedText(z.clave)
    const nombre = trimmedText(z.nombre)
    return clave && nombre ? [{ clave, nombre }] : []
  })
}

function parseItem(raw: unknown): MeteoWarning | null {
  if (!isRecord(raw)) return null
  const id = trimmedText(raw.id)
  const tipo = raw.tipo
  if (!id || typeof tipo !== 'string' || !(METEO_WARNING_TYPES as readonly string[]).includes(tipo))
    return null
  const severidad = METEO_WARNING_TYPES.indexOf(tipo as MeteoWarningType) + 1
  const zonas = parseZones(raw.zonas)
  return {
    id,
    tipo: tipo as MeteoWarningType,
    severidad: severidad as 1 | 2 | 3,
    codigo: trimmedText(raw.codigo),
    fenomeno: trimmedText(raw.fenomeno),
    titulo: trimmedText(raw.titulo),
    emision: isoOrNull(raw.emision),
    desde: isoOrNull(raw.desde),
    desde_texto: trimmedText(raw.desde_texto),
    hasta: isoOrNull(raw.hasta),
    hasta_texto: trimmedText(raw.hasta_texto),
    en_curso: raw.en_curso === true,
    observacion: trimmedText(raw.observacion),
    alcance: raw.alcance === 'zonas' && zonas.length > 0 ? 'zonas' : 'region',
    zonas,
    tabla: parseTable(raw.tabla),
    url: dmcUrl(raw.url),
  }
}

function parseSource(raw: unknown): MeteoWarningSource {
  const source = isRecord(raw) ? raw : {}
  const estado = source.estado
  return {
    collector: trimmedText(source.collector) ?? 'dmc_avisos',
    estado:
      typeof estado === 'string' && (HEALTH_STATUSES as readonly string[]).includes(estado)
        ? (estado as HealthStatus)
        : undefined,
    ultima_corrida: trimmedText(source.ultima_corrida),
    detalle: trimmedText(source.detalle),
  }
}

export function parseMeteoWarnings(payload: unknown): MeteoWarningResponse {
  const body = isRecord(payload) ? payload : {}
  const rawItems = Array.isArray(body.items) ? body.items : []
  const items = rawItems.map(parseItem).filter((item): item is MeteoWarning => item !== null)
  return {
    generado_en: trimmedText(body.generado_en) ?? '',
    total: items.length,
    items,
    fuente: parseSource(body.fuente),
  }
}

export async function fetchMeteoWarnings(signal?: AbortSignal): Promise<MeteoWarningResponse> {
  const payload = await apiGet<unknown>('/feed/avisos-meteo', signal)
  return parseMeteoWarnings(payload)
}
