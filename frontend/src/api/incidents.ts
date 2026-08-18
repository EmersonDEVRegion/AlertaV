/** Endpoints de incidentes. Una funcion por ruta del backend. */

import { apiGet, buildQuery } from './client'
import type {
  ActiveIncidentsQuery,
  Incident,
  IncidentDetail,
  IncidentStats,
} from './types'

/**
 * `GET /api/v1/incidents/active`
 *
 * Devuelve los incidentes consolidados, no las señales crudas. `/events` existe
 * para calibrar y auditar; el mapa operativo se dibuja con esto.
 */
export function fetchActiveIncidents(
  params: ActiveIncidentsQuery = {},
  signal?: AbortSignal,
): Promise<Incident[]> {
  return apiGet<Incident[]>(
    `/incidents/active${buildQuery({ ...params })}`,
    signal,
  )
}

/** `GET /api/v1/incidents/{code}` — acepta folio (INC-…) o public_id. */
export function fetchIncidentDetail(
  code: string,
  signal?: AbortSignal,
): Promise<IncidentDetail> {
  return apiGet<IncidentDetail>(`/incidents/${encodeURIComponent(code)}`, signal)
}

/** `GET /api/v1/incidents/stats` */
export function fetchIncidentStats(
  hours?: number,
  signal?: AbortSignal,
): Promise<IncidentStats> {
  return apiGet<IncidentStats>(`/incidents/stats${buildQuery({ hours })}`, signal)
}
