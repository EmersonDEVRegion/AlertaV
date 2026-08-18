/** Endpoint de reportes ciudadanos. */

import { apiPost } from './client'
import type { CitizenReportPayload, RawEvent } from './types'

/** Limites declarados por `CitizenReportCreate` en el backend. */
export const REPORT_TEXT_MIN = 3
export const REPORT_TEXT_MAX = 2000

/**
 * `POST /api/v1/events/citizen-report`
 *
 * Devuelve el evento crudo recien creado (201), no un incidente: el reporte
 * entra como una señal mas al motor de correlacion.
 */
export function submitCitizenReport(
  payload: CitizenReportPayload,
  signal?: AbortSignal,
): Promise<RawEvent> {
  return apiPost<RawEvent>('/events/citizen-report', payload, signal)
}
