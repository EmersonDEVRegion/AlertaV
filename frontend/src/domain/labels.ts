/** Nombres legibles en es-CL para los valores de enum del backend. */

import type {
  EventSource,
  IncidentStatus,
  IncidentType,
  LinkMethod,
} from '@/api/types'

export const SOURCE_LABEL: Record<EventSource, string> = {
  conaf: 'CONAF',
  senapred: 'SENAPRED',
  bomberos: 'Bomberos',
  nasa_firms: 'NASA FIRMS',
  broadcastify: 'Broadcastify',
  citizen: 'Reporte ciudadano',
  municipality: 'Municipalidad',
  media: 'Prensa',
  social_media: 'Redes sociales',
  weather: 'Meteorología',
  camera: 'Cámara',
  usgs: 'USGS (sismos)',
  csn: 'CSN (sismos)',
  waze: 'Waze (conductores)',
  transporte_informa: 'Transporte Informa (MTT)',
  chilquinta: 'Chilquinta',
  cge: 'CGE',
  mop: 'Vialidad (MOP)',
  other: 'Otra fuente',
}

/**
 * `possible_fire` se rotula así a propósito. Es un racimo de indicios (humo,
 * anomalia termica) que ninguna fuente confirmo; llamarlo "incendio" seria
 * afirmar algo que nadie declaro.
 */
export const TYPE_LABEL: Record<IncidentType, string> = {
  possible_fire: 'Posible incendio',
  wildfire: 'Incendio forestal',
  structural_fire: 'Incendio estructural',
  flood: 'Inundación',
  landslide: 'Derrumbe',
  accident: 'Accidente',
  power_outage: 'Corte de suministro',
  rescue: 'Rescate',
  other: 'Otro',
}

export const STATUS_LABEL: Record<IncidentStatus, string> = {
  active: 'Activo',
  controlled: 'Controlado',
  extinguished: 'Extinguido',
  // Precision deliberada: nadie declaro el fin de la emergencia, solo dejaron
  // de llegar señales.
  stale: 'Sin señales recientes',
  merged: 'Fusionado con otro incidente',
  dismissed: 'Descartado',
}

export const LINK_METHOD_LABEL: Record<LinkMethod, string> = {
  spatial: 'Coincidencia geográfica',
  commune_text: 'Coincidencia por comuna',
  manual: 'Vínculo manual',
}

export function sourceLabel(source: string): string {
  return SOURCE_LABEL[source as EventSource] ?? source
}
