/**
 * Endpoints de notificaciones push. Espejo de `app/schemas/push.py`.
 */

import { apiGet, apiPost } from './client'

export interface PushServerStatus {
  /** ¿El servidor está enviando avisos? */
  enabled: boolean
  /**
   * `applicationServerKey` para `pushManager.subscribe`. Puede venir aunque
   * `enabled` sea false: con los avisos pausados el servidor sigue aceptando
   * suscripciones.
   */
  public_key: string | null
  reason: string | null
  incident_radius_m: number
  incident_min_confidence: number
  incident_min_sources: number
  seismic_min_magnitude: number
}

export interface PushSubscriptionRead {
  id: string
  lat: number
  lon: number
  radius_m: number
  notify_incidents: boolean
  notify_seismic: boolean
  location_updated_at: string
}

export interface PushSubscribePayload {
  /** `PushSubscription.toJSON()` tal cual lo entrega el navegador. */
  subscription: PushSubscriptionJSON
  lat: number
  lon: number
  accuracy_m: number | null
  notify_incidents: boolean
  notify_seismic: boolean
}

export interface PushProbeResult {
  sent: boolean
  detail: string
}

export function fetchPushStatus(signal?: AbortSignal): Promise<PushServerStatus> {
  return apiGet<PushServerStatus>('/push/status', signal)
}

export function registerPushSubscription(
  payload: PushSubscribePayload,
): Promise<PushSubscriptionRead> {
  return apiPost<PushSubscriptionRead>('/push/subscriptions', payload)
}

export function unregisterPushSubscription(endpoint: string): Promise<void> {
  return apiPost<void>('/push/unsubscribe', { endpoint })
}

export function sendPushProbe(endpoint: string): Promise<PushProbeResult> {
  return apiPost<PushProbeResult>('/push/test', { endpoint })
}
