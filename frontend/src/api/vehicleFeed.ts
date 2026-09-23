/**
 * Cliente del radar de vehículos: `GET /api/v1/feed/vehiculos`.
 *
 * La ruta va RELATIVA a `env.apiBaseUrl`, que ya termina en `/api/v1`. Ver la
 * nota de `roadClosures.ts`: escribirla absoluta producía
 * `/api/v1/api/v1/…` y un 404.
 *
 * # Por qué se valida la respuesta
 *
 * El backend ya es tolerante: descarta las filas con `raw_data` inesperado. Acá
 * se repite la idea del lado del cliente, por la misma razón: un aviso mal
 * formado no puede tumbar el panel entero. Se descarta y se avisa **una** vez
 * por consola, que es donde alguien que depura lo va a buscar.
 */

import { apiGet, buildQuery } from './client'
import type { HealthStatus } from './health'
import {
  VEHICLE_LOCATIONS,
  VEHICLE_STATUSES,
  type VehicleFeedItem,
  type VehicleFeedQuery,
  type VehicleFeedResponse,
  type VehicleFeedSource,
  type VehicleLocation,
  type VehicleStatus,
} from './vehicleFeedTypes'

const HEALTH_STATUSES: readonly HealthStatus[] = ['ok', 'degraded', 'failing', 'stale', 'never']

let warnedAboutItems = false

type Raw = Record<string, unknown>

function isRecord(value: unknown): value is Raw {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : null
}

function oneOf<T extends string>(value: unknown, options: readonly T[]): T | null {
  return typeof value === 'string' && (options as readonly string[]).includes(value)
    ? (value as T)
    : null
}

/** Un ítem crudo → uno tipado, o `null` si le falta algo sin lo que no se puede mostrar. */
function parseItem(raw: unknown): VehicleFeedItem | null {
  if (!isRecord(raw)) return null

  const id = text(raw.id)
  const estado = oneOf<VehicleStatus>(raw.estado, VEHICLE_STATUSES)
  const region = oneOf<VehicleLocation>(raw.region, VEHICLE_LOCATIONS)
  const detectado = text(raw.detectado_en)
  // Sin estado, sin fecha de detección o fuera de la región no hay tarjeta
  // posible: no se sabe qué decir ni si cae en la ventana.
  if (!id || !estado || !region || !detectado || !Number.isFinite(Date.parse(detectado))) {
    return null
  }

  const anio = typeof raw.anio === 'number' && Number.isInteger(raw.anio) ? raw.anio : null

  return {
    id,
    estado,
    patente: text(raw.patente)?.toUpperCase() ?? null,
    tipo_vehiculo: text(raw.tipo_vehiculo),
    marca: text(raw.marca),
    modelo: text(raw.modelo),
    color: text(raw.color),
    anio,
    delito: text(raw.delito),
    lugar: text(raw.lugar),
    comuna: text(raw.comuna),
    region,
    recuperado_en: text(raw.recuperado_en),
    autoridad: text(raw.autoridad),
    tiempo_abandono: text(raw.tiempo_abandono),
    fecha_delito: text(raw.fecha_delito),
    fecha_precision: raw.fecha_precision === 'dia' ? 'dia' : 'deteccion',
    detectado_en: detectado,
    url_fuente: text(raw.url_fuente) ?? '',
  }
}

function parseSource(raw: unknown): VehicleFeedSource {
  const source = isRecord(raw) ? raw : {}
  return {
    collector: text(source.collector) ?? 'gbv_vehiculos',
    estado: oneOf<HealthStatus>(source.estado, HEALTH_STATUSES) ?? undefined,
    ultima_corrida: text(source.ultima_corrida),
    detalle: text(source.detalle),
  }
}

export function parseVehicleFeed(payload: unknown): VehicleFeedResponse {
  const body = isRecord(payload) ? payload : {}
  const rawItems = Array.isArray(body.items) ? body.items : []

  const items: VehicleFeedItem[] = []
  let dropped = 0
  for (const raw of rawItems) {
    const item = parseItem(raw)
    if (item) items.push(item)
    else dropped += 1
  }

  if (dropped > 0 && !warnedAboutItems) {
    warnedAboutItems = true
    console.warn(
      `[AlertaV/vehículos] Se descartaron ${dropped} aviso(s) con forma inesperada. ` +
        'Revisa `VehicleFeedItem` en backend/app/schemas/vehicle_feed.py: este ' +
        'cliente exige `id`, `estado`, `region` y `detectado_en`.',
    )
  }

  return {
    generado_en: text(body.generado_en) ?? '',
    horas: typeof body.horas === 'number' ? body.horas : 0,
    total: items.length,
    items,
    fuente: parseSource(body.fuente),
  }
}

export async function fetchVehicleFeed(
  params: VehicleFeedQuery = {},
  signal?: AbortSignal,
): Promise<VehicleFeedResponse> {
  const query = buildQuery({ horas: params.horas, limit: params.limit })
  const payload = await apiGet<unknown>(`/feed/vehiculos${query}`, signal)
  return parseVehicleFeed(payload)
}

/** Para los tests: el aviso de consola es uno por sesión. */
export function resetVehicleFeedWarnings(): void {
  warnedAboutItems = false
}
