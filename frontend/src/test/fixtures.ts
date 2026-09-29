/** Datos mínimos para montar componentes en los tests. */

import type { Incident } from '@/api/types'
import type { VehicleFeedItem } from '@/api/vehicleFeedTypes'
import type { WaterCut } from '@/api/waterCutTypes'
import type { IncidentLayerKey } from '@/domain/families'

export function makeIncident(over: Partial<Incident> = {}): Incident {
  return {
    code: 'INC-2026-00001',
    public_id: '3f2b6c1e-0000-4000-8000-000000000001',
    type: 'wildfire',
    status: 'active',
    lat: -33.05,
    lon: -71.62,
    confidence: 0.9,
    is_official_confirmed: true,
    alert_confidence: 0,
    alert_level: null,
    title: null,
    commune: 'Viña del Mar',
    province: 'Valparaíso',
    event_count: 2,
    source_count: 2,
    sources: ['conaf'],
    first_seen_at: '2026-08-20T12:00:00Z',
    last_seen_at: '2026-08-20T12:00:00Z',
    resolved_at: null,
    correlated_at: '2026-08-20T12:00:00Z',
    confidence_breakdown: {},
    outage: null,
    confidence_level: 'confirmed',
    confidence_label: 'confirmado',
    is_multi_source: true,
    ...over,
  }
}

export const emptyByLayer: Record<IncidentLayerKey, Incident[]> = {
  fire: [],
  traffic: [],
  power: [],
  otros: [],
}

/** Reloj fijo de los tests del radar: 23-sep-2026, 15:00 en Chile. */
export const VEHICLE_NOW = Date.parse('2026-09-23T18:00:00Z')

/** Un aviso de GBV visto hace `hoursAgo` horas respecto de `VEHICLE_NOW`. */
export function makeVehicle(
  over: Partial<VehicleFeedItem> = {},
  hoursAgo = 1,
): VehicleFeedItem {
  return {
    id: '7b1c2d3e-0000-4000-8000-000000000001',
    estado: 'robado',
    patente: 'LKXV55',
    tipo_vehiculo: 'Auto',
    marca: 'Toyota',
    modelo: 'Rav4',
    color: 'Negro Mica',
    anio: 2019,
    delito: 'Robo desde vía pública',
    lugar: 'los carrera 1200',
    comuna: 'Quilpué',
    region: 'v_region',
    recuperado_en: null,
    autoridad: null,
    tiempo_abandono: null,
    fecha_delito: '2026-09-22',
    fecha_precision: 'dia',
    detectado_en: new Date(VEHICLE_NOW - hoursAgo * 3_600_000).toISOString(),
    url_fuente: 'https://gbvspa.cl/denuncias/auto-robo-desde-via-publica-lkxv55-6784',
    ...over,
  }
}

/**
 * Un corte de agua como lo entrega `/events/water-cuts/geojson`, ya aplanado.
 * Es el de Viña del 23-09 de las capturas reales del backend.
 */
export function makeWaterCut(over: Partial<WaterCut> = {}): WaterCut {
  return {
    id: '5d0c1a2b-0000-4000-8000-000000000001',
    sisda: '2916567',
    comuna: 'Viña del Mar',
    tipo: 'emergencia',
    programado: false,
    motivo: 'Vida util vencida',
    calles: 'LOS PENSAMIENTOS',
    sector: null,
    inicio: '2026-09-23T14:00:00+00:00',
    fin: '2026-09-23T20:00:00+00:00',
    suministro_alternativo: false,
    url_mapa: 'https://tupuntodeagua.esval.cl/?sisda=2916567',
    visto_en: '2026-09-23T18:30:00+00:00',
    coordinates: [-71.5412, -33.0213],
    ...over,
  }
}
