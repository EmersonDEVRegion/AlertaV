/**
 * Espejo TypeScript del contrato sísmico.
 *
 * Fuente de verdad: `backend/app/schemas/seismic.py`.
 *
 * Un sismo **no es un incidente**: no pasa por el motor de correlación, no
 * tiene `confidence` ni `status`, y no comparte ningún campo con `Incident`.
 * Por eso vive en su propio archivo en vez de colgarse del contrato de
 * incidentes — mezclarlos invitaría a reutilizar lógica que no aplica.
 */

/** Nivel PAGER de impacto estimado del USGS. */
export type PagerAlert = 'green' | 'yellow' | 'orange' | 'red'

/**
 * `automatic` = solución de máquina, sin revisar; la magnitud puede corregirse.
 * `reviewed` = revisada por un sismólogo.
 */
export type ReviewStatus = 'automatic' | 'reviewed'

/** `SeismicEventRead` — `GET /api/v1/events/seismic`. */
export interface SeismicEvent {
  public_id: string
  usgs_id: string
  /** Hora de origen del sismo, en UTC. */
  timestamp: string

  lat: number
  lon: number

  /**
   * Puede venir `null`: el USGS publica la detección antes de terminar de
   * calcular la magnitud. Todo lo que dependa de este valor tiene que preverlo.
   */
  magnitude: number | null
  mag_type: string | null
  /** Profundidad del hipocentro. Puede ser negativa: se mide desde el nivel del mar. */
  depth_km: number | null

  place: string | null
  commune: string | null
  province: string | null

  felt_reports: number | null
  /**
   * Bandera del USGS: el evento cumple criterios para evaluación de tsunami.
   * NO es una alerta vigente en Chile — eso lo declara SENAPRED y viaja por
   * `alert_level` en los incidentes.
   */
  tsunami: boolean
  pager_alert: PagerAlert | null
  significance: number | null
  review_status: ReviewStatus | null
  usgs_url: string | null
}

/** `SeismicStats` — `GET /api/v1/events/seismic/stats`. */
export interface SeismicStats {
  total: number
  max_magnitude: number | null
  felt_count: number
  tsunami_flagged: number
}

/** Parámetros de `GET /api/v1/events/seismic`. */
export interface SeismicQuery {
  hours?: number
  min_magnitude?: number
  max_depth_km?: number
  tsunami_only?: boolean
  limit?: number
  offset?: number
}
