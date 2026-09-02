/** Frescura de la recolección: lo que impide que un cero mienta. */

import { apiGet } from './client'
import type { IncidentLayerKey } from '@/domain/families'

/**
 * Estado de una fuente o de una familia.
 *
 * `ok` es el único que autoriza a leer un contador en cero como «no pasó nada».
 * Los otros cuatro significan lo contrario, y se distinguen entre sí porque
 * piden acciones distintas:
 *
 * * `degraded` — la fuente corrió y sabe que no ve el presente. Es el caso del
 *   Actor de Apify detenido: hay datos, son viejos, y el collector lo declara.
 * * `failing` — la última corrida falló.
 * * `stale` — hace demasiado que no corre. La cadencia la declara el propio
 *   collector; el backend tolera tres antes de decirlo.
 * * `never` — no hay ninguna corrida registrada.
 */
export type HealthStatus = 'ok' | 'degraded' | 'failing' | 'stale' | 'never'

export interface CollectorHealth {
  collector: string
  families: IncidentLayerKey[]
  status: HealthStatus
  last_run_at: string | null
  age_seconds: number | null
  expected_interval_seconds: number
  /** El mensaje de la corrida: es lo que explica la ceguera en palabras. */
  detail: string | null
}

export interface CollectorsHealth {
  generated_at: string
  by_family: Record<IncidentLayerKey, HealthStatus>
  collectors: CollectorHealth[]
}

/** `GET /api/v1/collectors/health` */
export function fetchCollectorsHealth(signal?: AbortSignal): Promise<CollectorsHealth> {
  return apiGet<CollectorsHealth>('/collectors/health', signal)
}
