/** Endpoints de sismos. Una función por ruta del backend. */

import { apiGet, buildQuery } from './client'
import type { SeismicEvent, SeismicQuery, SeismicStats } from './seismicTypes'

/**
 * `GET /api/v1/events/seismic`
 *
 * Se consume el listado tipado y no `/seismic/geojson`, por el mismo criterio
 * que con los incidentes: la ficha necesita el objeto completo igual, y
 * mantener dos vistas del mismo dato es la forma segura de que se
 * desincronicen. El GeoJSON del mapa se arma en el cliente.
 */
export function fetchSeismicEvents(
  params: SeismicQuery = {},
  signal?: AbortSignal,
): Promise<SeismicEvent[]> {
  return apiGet<SeismicEvent[]>(`/events/seismic${buildQuery({ ...params })}`, signal)
}

/** `GET /api/v1/events/seismic/stats` */
export function fetchSeismicStats(
  hours?: number,
  signal?: AbortSignal,
): Promise<SeismicStats> {
  return apiGet<SeismicStats>(`/events/seismic/stats${buildQuery({ hours })}`, signal)
}
