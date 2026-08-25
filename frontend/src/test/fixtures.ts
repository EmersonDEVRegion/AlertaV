/** Datos mínimos para montar componentes en los tests. */

import type { Incident } from '@/api/types'
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
