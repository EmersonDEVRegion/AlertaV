/**
 * Contrato de `GET /api/v1/feed/vehiculos`.
 *
 * Espejo de `backend/app/schemas/vehicle_feed.py`. Si cambia uno, cambia el
 * otro.
 *
 * # Lo que NO es
 *
 * No es un incidente ni una señal del mapa. Estos avisos no traen coordenadas
 * ni confianza, y `lugar` es texto libre de GBV, no una dirección
 * geocodificada. Por eso viven en un panel propio y no como pines.
 *
 * # Los dos tiempos, que no son intercambiables
 *
 *   - `detectado_en` es cuándo AlertaV vio el aviso por primera vez. Es lo que
 *     mide la ventana de 48 h y lo único que autoriza a decir «hace 3 h».
 *   - `fecha_delito` es el día del robo según GBV, sin hora. En un recuperado
 *     sigue siendo la fecha del ROBO, no la de la recuperación.
 */

import type { HealthStatus } from './health'

export const VEHICLE_STATUSES = ['robado', 'recuperado', 'abandonado'] as const
export type VehicleStatus = (typeof VEHICLE_STATUSES)[number]

/** `otra` existe en el backend pero nunca llega a este feed. */
export const VEHICLE_LOCATIONS = ['v_region', 'sin_ubicar'] as const
export type VehicleLocation = (typeof VEHICLE_LOCATIONS)[number]

export interface VehicleFeedItem {
  /** `public_id` del evento. */
  id: string
  estado: VehicleStatus
  /** Normalizada (`LKXV55`). `null` en todos los abandonados y en patentes mal tipeadas en origen. */
  patente: string | null
  tipo_vehiculo: string | null
  marca: string | null
  modelo: string | null
  color: string | null
  anio: number | null
  delito: string | null
  /** Texto libre tal como lo publicó GBV. */
  lugar: string | null
  /** Comuna de la V Región deducida del texto. `null` si no se pudo. */
  comuna: string | null
  region: VehicleLocation
  recuperado_en: string | null
  autoridad: string | null
  /** Relativo, como lo publica GBV: `3 semanas`. */
  tiempo_abandono: string | null
  /** `AAAA-MM-DD`, sin hora. */
  fecha_delito: string | null
  fecha_precision: 'dia' | 'deteccion'
  /** ISO 8601 con zona. */
  detectado_en: string
  url_fuente: string
}

export interface VehicleFeedSource {
  collector: string
  /**
   * Mismas reglas que `/collectors/health`. `undefined` si llegó un valor que
   * este cliente no conoce: no saber no autoriza a afirmar que la fuente está
   * ciega (ver `shouldWarn` en `LayerHealth`).
   */
  estado: HealthStatus | undefined
  ultima_corrida: string | null
  detalle: string | null
}

export interface VehicleFeedResponse {
  generado_en: string
  horas: number
  total: number
  items: VehicleFeedItem[]
  fuente: VehicleFeedSource
}

export interface VehicleFeedQuery {
  horas?: number
  limit?: number
}
