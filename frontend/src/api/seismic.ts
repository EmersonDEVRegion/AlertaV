/** Endpoints de sismos. Una función por ruta del backend. */

import { apiGet, buildQuery } from './client'
import type { SeismicEvent, SeismicQuery } from './seismicTypes'

/**
 * `GET /api/v1/events/seismic`
 *
 * Se consume el listado tipado: la ficha necesita el objeto completo, y el
 * GeoJSON del mapa se arma en el cliente. (El backend ya no sirve
 * `/seismic/geojson` ni `/seismic/stats`.)
 */
export function fetchSeismicEvents(
  params: SeismicQuery = {},
  signal?: AbortSignal,
): Promise<SeismicEvent[]> {
  return apiGet<SeismicEvent[]>(`/events/seismic${buildQuery({ ...params })}`, signal)
}
