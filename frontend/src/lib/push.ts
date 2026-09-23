/**
 * Piezas puras de los avisos push: qué soporta el navegador, cuándo volver a
 * informar la ubicación y cómo leer el enlace de una notificación.
 *
 * Todo lo que toca APIs del navegador (permisos, service worker, GPS) vive en
 * `hooks/usePushNotifications.ts`. Acá sólo hay decisiones, para poder
 * probarlas sin un navegador.
 */

import { distanceKm } from '@/lib/geo'

// ---------------------------------------------------------------------------
// Soporte
// ---------------------------------------------------------------------------

/**
 * - `supported`: puede suscribirse ya.
 * - `ios-install`: iPhone o iPad fuera de la app instalada. Safari sólo expone
 *   Web Push a las PWA agregadas a la pantalla de inicio (iOS 16.4+), así que
 *   lo que corresponde es explicar cómo instalarla, no decir «no soportado».
 * - `insecure`: sin HTTPS no hay service worker ni push.
 * - `unsupported`: navegador sin la API (o un WebView embebido).
 */
export type PushSupport = 'supported' | 'ios-install' | 'insecure' | 'unsupported'

export interface PushEnvironment {
  isSecureContext: boolean
  userAgent: string
  platform: string
  maxTouchPoints: number
  standalone: boolean
  hasServiceWorker: boolean
  hasPushManager: boolean
  hasNotification: boolean
}

export function detectPushSupport(env: PushEnvironment): PushSupport {
  if (!env.isSecureContext) return 'insecure'

  // iPadOS se presenta como «MacIntel» con pantalla táctil desde la versión 13.
  const isIOS =
    /iPad|iPhone|iPod/.test(env.userAgent) ||
    (env.platform === 'MacIntel' && env.maxTouchPoints > 1)
  if (isIOS && !env.standalone) return 'ios-install'

  if (!env.hasServiceWorker || !env.hasPushManager || !env.hasNotification) {
    return 'unsupported'
  }
  return 'supported'
}

/** Lee el entorno real. Separado de la decisión para poder probarla. */
export function readPushEnvironment(win: Window = window): PushEnvironment {
  const nav = win.navigator as Navigator & { standalone?: boolean }
  let standalone = nav.standalone === true
  try {
    standalone ||= win.matchMedia('(display-mode: standalone)').matches
  } catch {
    /* jsdom y navegadores viejos sin matchMedia */
  }
  return {
    isSecureContext: win.isSecureContext,
    userAgent: nav.userAgent,
    platform: nav.platform,
    maxTouchPoints: nav.maxTouchPoints ?? 0,
    standalone,
    hasServiceWorker: 'serviceWorker' in nav,
    hasPushManager: 'PushManager' in win,
    hasNotification: 'Notification' in win,
  }
}

// ---------------------------------------------------------------------------
// Claves
// ---------------------------------------------------------------------------

/** Clave VAPID en base64url → bytes, que es lo que pide `pushManager.subscribe`. */
export function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padded = base64.trim() + '='.repeat((4 - (base64.trim().length % 4)) % 4)
  const raw = atob(padded.replace(/-/g, '+').replace(/_/g, '/'))
  const bytes = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i)
  return bytes
}

/**
 * ¿La suscripción existente se creó con esta clave del servidor?
 *
 * Si alguien regenera las claves VAPID, el navegador conserva la suscripción
 * vieja y el servicio de push rechaza cada envío con 403. Detectarlo acá permite
 * rehacerla sola la próxima vez que se abra la app.
 */
export function sameServerKey(current: ArrayBuffer | null | undefined, expected: Uint8Array): boolean {
  if (!current) return false
  const a = new Uint8Array(current)
  if (a.length !== expected.length) return false
  return a.every((byte, i) => byte === expected[i])
}

// ---------------------------------------------------------------------------
// Memoria local de la sincronización
// ---------------------------------------------------------------------------

export interface PushMemo {
  /** Última vez que el servidor recibió la suscripción, en ms. */
  syncedAt: number
  lat: number
  lon: number
  notifyIncidents: boolean
  notifySeismic: boolean
}

const MEMO_KEY = 'alertav:push'
const INVITE_KEY = 'alertav:push-invite-dismissed'

/**
 * `localStorage` puede no existir o lanzar (navegación privada, almacenamiento
 * bloqueado). Nada de esto es imprescindible: sin memoria, la app sólo
 * sincroniza un poco más seguido.
 */
function storage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

export function loadPushMemo(): PushMemo | null {
  try {
    const raw = storage()?.getItem(MEMO_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PushMemo>
    if (
      typeof parsed.syncedAt !== 'number' ||
      typeof parsed.lat !== 'number' ||
      typeof parsed.lon !== 'number'
    ) {
      return null
    }
    return {
      syncedAt: parsed.syncedAt,
      lat: parsed.lat,
      lon: parsed.lon,
      notifyIncidents: parsed.notifyIncidents !== false,
      notifySeismic: parsed.notifySeismic !== false,
    }
  } catch {
    return null
  }
}

export function savePushMemo(memo: PushMemo): void {
  try {
    storage()?.setItem(MEMO_KEY, JSON.stringify(memo))
  } catch {
    /* sin memoria local; ver `storage()` */
  }
}

export function clearPushMemo(): void {
  try {
    storage()?.removeItem(MEMO_KEY)
  } catch {
    /* ídem */
  }
}

export function inviteDismissed(): boolean {
  try {
    return storage()?.getItem(INVITE_KEY) === '1'
  } catch {
    return false
  }
}

export function dismissInvite(): void {
  try {
    storage()?.setItem(INVITE_KEY, '1')
  } catch {
    /* ídem */
  }
}

// ---------------------------------------------------------------------------
// ¿Hay que volver a informar la ubicación?
// ---------------------------------------------------------------------------

/** Moverse menos que esto no cambia a quién le llega un aviso de 5 km. */
export const RESYNC_DISTANCE_KM = 0.25
/**
 * Aunque no se haya movido, se reenvía cada tanto: renueva la suscripción en el
 * servidor si éste la perdió (una base restaurada, una baja por fallos) y
 * mantiene al día las claves si el navegador las rotó.
 */
export const RESYNC_MAX_AGE_MS = 12 * 60 * 60 * 1000
/** Pedir el GPS más seguido que esto al abrir la app sólo gasta batería. */
export const RESYNC_MIN_INTERVAL_MS = 10 * 60 * 1000

export function shouldResync(
  memo: PushMemo | null,
  next: { lat: number; lon: number } | null,
  now: number,
): boolean {
  if (memo === null) return true
  const age = now - memo.syncedAt
  if (age >= RESYNC_MAX_AGE_MS) return true
  if (next === null) return false
  if (age < RESYNC_MIN_INTERVAL_MS) return false
  return distanceKm(memo.lat, memo.lon, next.lat, next.lon) >= RESYNC_DISTANCE_KM
}

// ---------------------------------------------------------------------------
// Enlaces de las notificaciones
// ---------------------------------------------------------------------------

export type PushDeepLink =
  | { kind: 'incident'; code: string }
  | { kind: 'seismic'; key: string; lat: number; lon: number }

/**
 * Lee el destino de una notificación: `/?incidente=INC-2026-00142` o
 * `/?sismo=csn:379889&lat=-33.02&lon=-71.9`. Lo arma el backend en
 * `app/services/push/messages.py`.
 */
export function parseDeepLink(href: string, base = 'https://alertav.invalid'): PushDeepLink | null {
  let url: URL
  try {
    url = new URL(href, base)
  } catch {
    return null
  }
  const code = url.searchParams.get('incidente')
  if (code && /^[A-Z]{2,5}-\d{4}-\d{1,8}$/.test(code)) {
    return { kind: 'incident', code }
  }
  const key = url.searchParams.get('sismo')
  const lat = Number(url.searchParams.get('lat'))
  const lon = Number(url.searchParams.get('lon'))
  if (
    key &&
    Number.isFinite(lat) &&
    Number.isFinite(lon) &&
    Math.abs(lat) <= 90 &&
    Math.abs(lon) <= 180 &&
    url.searchParams.get('lat') !== null &&
    url.searchParams.get('lon') !== null
  ) {
    return { kind: 'seismic', key, lat, lon }
  }
  return null
}

/** `usgs:us6000tlm3` → `us6000tlm3`, que es el `usgs_id` de la capa de sismos. */
export function usgsIdOf(key: string): string | null {
  return key.startsWith('usgs:') ? key.slice(5) : null
}
