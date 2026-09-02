/**
 * Espejo TypeScript del contrato del backend.
 *
 * Fuente de verdad: `backend/app/schemas/incident.py` y `backend/app/models/enums.py`.
 * Si cambia alla, cambia aca. No se inventan campos ni se relajan tipos: el
 * objetivo es que un cambio de contrato rompa la compilacion en vez de romper el
 * mapa en produccion.
 */

import type { ReportCategory } from '@/domain/reportCategories'

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
  'usgs',
  // Capa de accidentes viales. Sin estas dos entradas, `SOURCE_LABEL` deja de
  // ser exhaustivo y las fichas muestran `undefined` como nombre de fuente.
  'waze',
  'transporte_informa',
  // Sismología nacional y distribuidoras eléctricas.
  'csn',
  'chilquinta',
  'cge',
  // Dirección de Vialidad (MOP): rutas dañadas, no siniestros. Emite
  // `road_closure` con confianza 0, así que NUNCA aparece en el desglose por
  // fuente de un incidente — sólo en la capa de contexto del mapa.
  'mop',
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
  'power_outage',
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

/**
 * `ConfidenceLevel` — tramo operativo que decide el color del mapa (política v2.0.0).
 *
 * **No es `is_official_confirmed`.** `confirmed` acá significa "la evidencia
 * acumulada supera el 60 %", que es un juicio del motor de correlación.
 * `is_official_confirmed` significa "CONAF o Bomberos fueron al lugar", que es un
 * hecho institucional. Un racimo de despachos radiales llega a `confirmed` con
 * `is_official_confirmed = false`, y la UI tiene que dejar ver la diferencia.
 */
export const CONFIDENCE_LEVELS = ['unsafe', 'possible', 'confirmed'] as const
export type ConfidenceLevel = (typeof CONFIDENCE_LEVELS)[number]

/**
 * Cortes de los tramos. Espejo de `UNSAFE_THRESHOLD` / `CONFIRMED_THRESHOLD` en
 * `backend/app/models/enums.py`.
 *
 * El backend es la autoridad: el cliente sólo recalcula el tramo cuando la
 * respuesta no trae `confidence_level` (por ejemplo, una respuesta vieja
 * servida desde la caché del service worker).
 */
export const UNSAFE_THRESHOLD = 0.3
export const CONFIRMED_THRESHOLD = 0.6

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

/** Proveedores de suministro con cobertura en la Región de Valparaíso. */
export const OUTAGE_PROVIDERS = ['chilquinta', 'cge'] as const
export type OutageProvider = (typeof OUTAGE_PROVIDERS)[number]

/**
 * `OutageDetail` — metadatos de un corte de suministro.
 *
 * Sólo viene poblado en incidentes de tipo `power_outage`; `null` en cualquier
 * otra familia. Los tres campos que importan pueden faltar por separado: los
 * feeds publican el corte antes de contar clientes o de estimar la reposición,
 * así que nada acá puede asumirse presente.
 */
export interface OutageDetail {
  provider: OutageProvider | string
  /** Suma de clientes de todas las señales. `null` si ninguna lo informó. */
  affected_clients: number | null
  /** Reposición estimada más tardía, en ISO 8601. */
  estimated_restoration: string | null
  sector: string | null
  outage_count: number
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

  /** Sólo en `power_outage`. `null` en el resto de las familias. */
  outage: OutageDetail | null

  // Campos calculados por el backend (`computed_field`).
  /**
   * Tramo operativo derivado de `confidence`, no de `is_official_confirmed`.
   * Es lo que decide el color del pin.
   */
  confidence_level: ConfidenceLevel
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
  /**
   * Qué está reportando la persona. Obligatorio: sin categoria el motor no sabe
   * con que familia de fenomeno correlacionar la señal, y el backend responde
   * 422. Ver `domain/reportCategories.ts`.
   */
  category: ReportCategory
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
