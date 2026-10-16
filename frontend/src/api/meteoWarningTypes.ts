/**
 * Contrato de `GET /api/v1/feed/avisos-meteo`.
 *
 * Espejo de `backend/app/schemas/meteo_warning.py`. Si cambia uno, cambia el
 * otro.
 *
 * Un aviso de la DMC es un **pronóstico**: dice lo que puede ocurrir, no lo
 * que ocurrió. Y no es una alerta de SENAPRED: la interfaz lo nombra siempre
 * por su institución («DMC · Aviso»).
 */

import type { HealthStatus } from './health'

export const METEO_WARNING_TYPES = ['Aviso', 'Alerta', 'Alarma'] as const
export type MeteoWarningType = (typeof METEO_WARNING_TYPES)[number]

export interface MeteoWarningZone {
  clave: string
  nombre: string
}

export interface MeteoWarningTable {
  titulo: string | null
  columnas: string[]
  filas: { zona: string; valores: string[] }[]
}

export interface MeteoWarning {
  id: string
  tipo: MeteoWarningType
  /** 1 = Aviso, 2 = Alerta, 3 = Alarma. */
  severidad: 1 | 2 | 3
  codigo: string | null
  fenomeno: string | null
  titulo: string | null
  emision: string | null
  /** Inicio del tramo que nombra la DMC («la tarde» → 12:00). Lectura, no dato. */
  desde: string | null
  desde_texto: string | null
  /** Fin del tramo («la noche del martes» → miércoles 06:00). */
  hasta: string | null
  hasta_texto: string | null
  en_curso: boolean
  observacion: string | null
  alcance: 'region' | 'zonas'
  zonas: MeteoWarningZone[]
  tabla: MeteoWarningTable | null
  /** El mapa oficial de la DMC: se enlaza para citarla. */
  url: string
}

export interface MeteoWarningSource {
  collector: string
  estado?: HealthStatus
  ultima_corrida: string | null
  detalle: string | null
}

export interface MeteoWarningResponse {
  generado_en: string
  total: number
  items: MeteoWarning[]
  fuente: MeteoWarningSource
}
