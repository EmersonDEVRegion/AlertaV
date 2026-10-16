/** Nombres legibles en es-CL para los valores de enum del backend. */

import type {
  UbicacionPrecision,
  EventSource,
  IncidentStatus,
  IncidentType,
  LinkMethod,
} from '@/api/types'

const SOURCE_LABEL: Record<EventSource, string> = {
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
  gbv: 'GBV (vehículos)',
  esval: 'Esval (agua)',
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

/**
 * Qué tan fino es el punto del incidente, dicho en la ficha. `exacta` no
 * se dice: un punto con coordenadas propias no necesita aclaración.
 */
export const UBICACION_PRECISION_LABEL: Record<UbicacionPrecision, string | null> = {
  despacho: 'Ubicación: el punto que marcó la central de Bomberos al despachar.',
  intersection: 'Ubicación: en el cruce que informó la fuente (± 60 m).',
  address: 'Ubicación: en la dirección informada.',
  exacta: null,
  street:
    'Ubicación aproximada (± 300 m): sobre la calle informada, no necesariamente en la esquina.',
  sector: 'Ubicación aproximada (± 600 m): el sector informado, no una dirección.',
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
  sector_text: 'Coincidencia por sector',
  manual: 'Vínculo manual',
}

export function sourceLabel(source: string): string {
  return SOURCE_LABEL[source as EventSource] ?? source
}
