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
import { isRecord, trimmedText } from './parse'
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

function oneOf<T extends string>(value: unknown, options: readonly T[]): T | null {
  return typeof value === 'string' && (options as readonly string[]).includes(value)
    ? (value as T)
    : null
}

/** Un ítem crudo → uno tipado, o `null` si le falta algo sin lo que no se puede mostrar. */
function parseItem(raw: unknown): VehicleFeedItem | null {
  if (!isRecord(raw)) return null

  const id = trimmedText(raw.id)
  const estado = oneOf<VehicleStatus>(raw.estado, VEHICLE_STATUSES)
  const region = oneOf<VehicleLocation>(raw.region, VEHICLE_LOCATIONS)
  const detectado = trimmedText(raw.detectado_en)
  // Sin estado, sin fecha de detección o fuera de la región no hay tarjeta
  // posible: no se sabe qué decir ni si cae en la ventana.
  if (!id || !estado || !region || !detectado || !Number.isFinite(Date.parse(detectado))) {
    return null
  }

  const anio = typeof raw.anio === 'number' && Number.isInteger(raw.anio) ? raw.anio : null

  return {
    id,
    estado,
    patente: trimmedText(raw.patente)?.toUpperCase() ?? null,
    tipo_vehiculo: trimmedText(raw.tipo_vehiculo),
    marca: trimmedText(raw.marca),
    modelo: trimmedText(raw.modelo),
    color: trimmedText(raw.color),
    anio,
    delito: trimmedText(raw.delito),
    lugar: trimmedText(raw.lugar),
    comuna: trimmedText(raw.comuna),
    region,
    recuperado_en: trimmedText(raw.recuperado_en),
    autoridad: trimmedText(raw.autoridad),
    tiempo_abandono: trimmedText(raw.tiempo_abandono),
    fecha_delito: trimmedText(raw.fecha_delito),
    fecha_precision: raw.fecha_precision === 'dia' ? 'dia' : 'deteccion',
    detectado_en: detectado,
    url_fuente: trimmedText(raw.url_fuente) ?? '',
  }
}

function parseSource(raw: unknown): VehicleFeedSource {
  const source = isRecord(raw) ? raw : {}
  return {
    collector: trimmedText(source.collector) ?? 'gbv_vehiculos',
    estado: oneOf<HealthStatus>(source.estado, HEALTH_STATUSES) ?? undefined,
    ultima_corrida: trimmedText(source.ultima_corrida),
    detalle: trimmedText(source.detalle),
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
    generado_en: trimmedText(body.generado_en) ?? '',
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
