/**
 * Espejo TypeScript del contrato del backend.
 *
 * Fuente de verdad: `backend/app/schemas/incident.py` y `backend/app/models/enums.py`.
 * Si cambia alla, cambia aca. No se inventan campos ni se relajan tipos: el
 * objetivo es que un cambio de contrato rompa la compilacion en vez de romper el
 * mapa en produccion.
 */

/** `EventSource` — origen de una señal. */
export const EVENT_SOURCES = [
  'citizen',
  'broadcastify',
  'nasa_firms',
  'conaf',
  'senapred',
  'bomberos',
  'municipality',
  'media',
  'social_media',
  'weather',
  'camera',
  'other',
] as const
export type EventSource = (typeof EVENT_SOURCES)[number]

/**
 * `IncidentType` — naturaleza del FENOMENO consolidado.
 *
 * Ojo con `possible_fire`: es la pieza que impide que un racimo puramente
 * satelital o de avistamientos de humo se rotule como incendio. La UI nunca
 * debe mostrarlo como "incendio".
 */
export const INCIDENT_TYPES = [
  'possible_fire',
  'wildfire',
  'structural_fire',
  'flood',
  'landslide',
  'accident',
  'rescue',
  'other',
] as const
export type IncidentType = (typeof INCIDENT_TYPES)[number]

/**
 * `IncidentStatus` — ciclo de vida.
 *
 * `stale` NO es `extinguished`: significa que dejaron de llegar señales, no que
 * alguien haya declarado el fin de la emergencia. La UI los rotula distinto.
 */
export const INCIDENT_STATUSES = [
  'active',
  'controlled',
  'extinguished',
  'stale',
  'merged',
  'dismissed',
] as const
export type IncidentStatus = (typeof INCIDENT_STATUSES)[number]

export type AlertLevel = 'roja' | 'amarilla' | 'temprana_preventiva' | 'verde'

export type LinkMethod = 'spatial' | 'commune_text' | 'manual'

/** Etiquetas legibles que calcula el backend. No se recalculan en el cliente. */
export type ConfidenceLabel =
  | 'confirmado'
  | 'muy probable'
  | 'probable'
  | 'sin confirmar'

/** Traza auditable de como se calculo `confidence`. */
export interface ConfidenceBreakdown {
  policy_version?: string
  signals?: number
  by_source?: Record<
    string,
    {
      signals: number
      contribution: number
      ceiling: number
      confirming: boolean
    }
  >
  combined?: number
  ceiling_applied?: string | null
  alert?: { level: string | null; confidence: number }
  [key: string]: unknown
}

/** `IncidentRead` — lo que devuelve `GET /api/v1/incidents/active`. */
export interface Incident {
  code: string
  public_id: string
  type: IncidentType
  status: IncidentStatus

  lat: number
  lon: number

  /** Confianza en que el FENOMENO es real. Eje independiente de la alerta. */
  confidence: number
  /**
   * Unico booleano que autoriza a pintar algo como confirmado: alguien
   * (CONAF, Bomberos) fue al lugar. No derivar esto de un umbral de
   * `confidence`.
   */
  is_official_confirmed: boolean
  /** Confianza en el ESTADO DE ALERTA declarado por SENAPRED. */
  alert_confidence: number
  alert_level: AlertLevel | null

  title: string | null
  commune: string | null
  province: string | null

  event_count: number
  source_count: number
  sources: EventSource[]

  first_seen_at: string
  last_seen_at: string
  resolved_at: string | null
  correlated_at: string

  confidence_breakdown: ConfidenceBreakdown

  // Campos calculados por el backend (`computed_field`).
  confidence_label: ConfidenceLabel
  is_multi_source: boolean
}

/** Una señal y el motivo por el que quedo unida al incidente. */
export interface IncidentEventLink {
  raw_event_id: number
  public_id: string | null
  source: EventSource
  type: string
  timestamp: string
  confidence: number
  text: string | null
  lat: number | null
  lon: number | null

  link_method: LinkMethod
  link_confidence: number
  distance_m: number | null
  matched_commune: string | null
  note: string | null
}

/** `IncidentDetail` — `GET /api/v1/incidents/{code}`. */
export interface IncidentDetail extends Incident {
  events: IncidentEventLink[]
}

/** `IncidentStats` — `GET /api/v1/incidents/stats`. */
export interface IncidentStats {
  total: number
  confirmed: number
  with_official_alert: number
  avg_confidence: number | null
  last_seen_at: string | null
  by_status: Record<string, number>
  by_type: Record<string, number>
}

// --- Reporte ciudadano -------------------------------------------------------

/**
 * Cuerpo de `POST /api/v1/events/citizen-report`.
 *
 * Espejo de `CitizenReportCreate` (backend/app/schemas/event.py), que declara
 * `extra="forbid"`: cualquier campo de mas es un 422, no un campo ignorado.
 *
 * Lo que el cliente NO manda, a proposito: `source` y `confidence` los fija el
 * servidor. Si el cliente pudiera declararse `conaf` o asignarse confianza 1.0,
 * falsificar un incidente confirmado seria trivial.
 *
 * El backend acepta ademas `type`, `reported_at`, `accuracy_m` y `media_url`.
 * Se dejan fuera por ahora: `type` cae al default `smoke` del servidor y el
 * resto no tiene UI todavia.
 */
export interface CitizenReportPayload {
  lat: number
  lon: number
  /** Entre 3 y 2000 caracteres. El backend rechaza fuera de ese rango. */
  text: string
}

/**
 * `EventRead` — respuesta 201 del reporte ciudadano.
 *
 * Es una SEÑAL, no un incidente. Recien cuando el motor de correlacion corra
 * (cada 120 s) puede quedar unida a un incidente y aparecer en el mapa.
 */
export interface RawEvent {
  id: number
  public_id: string
  timestamp: string
  source: EventSource
  type: string
  lat: number | null
  lon: number | null
  text: string | null
  external_id: string | null
  confidence: number
  raw_data: Record<string, unknown>
  commune: string | null
  province: string | null
  ingested_at: string
  processed_at: string | null
  incident_id: number | null
}

/** Parametros de `GET /api/v1/incidents/active`. */
export interface ActiveIncidentsQuery {
  hours?: number
  type?: IncidentType[]
  status?: IncidentStatus[]
  min_confidence?: number
  commune?: string
  /** west,south,east,north en WGS84 */
  bbox?: string
  confirmed_only?: boolean
  with_alert_only?: boolean
  limit?: number
  offset?: number
}
