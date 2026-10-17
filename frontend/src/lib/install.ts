import { useSyncExternalStore } from 'react'

/**
 * Instalar AlertaV en la pantalla de inicio (§Q, 16-10-2026).
 *
 * Qué permite cada navegador:
 *
 *   - Chrome, Edge y Samsung Internet disparan `beforeinstallprompt`: se guarda
 *     y un botón propio abre el instalador nativo, de un toque.
 *   - Safari (iPhone, iPad, Mac) y Chrome en el iPhone no tienen API: se
 *     muestran los pasos.
 *   - Firefox de escritorio no instala apps web; un navegador dentro de
 *     WhatsApp o Instagram, tampoco. Ahí se sugiere abrirla en otro.
 *   - Ya instalada: no se muestra nada, nunca.
 *
 * La invitación aparece una sola vez, desde la segunda visita (quien vuelve ya
 * le encontró valor), y «✕» la retira para siempre. El ítem del menú queda.
 */

export type InstallPlatform =
  | 'installed'
  | 'prompt'
  | 'ios-safari'
  | 'ios-other'
  | 'android'
  | 'android-firefox'
  | 'desktop-chromium'
  | 'mac-safari'
  | 'firefox-desktop'
  | 'in-app'

export interface InstallEnvironment {
  userAgent: string
  platform: string
  maxTouchPoints: number
  standalone: boolean
  hasPrompt: boolean
}

/** Decisión pura: qué mostrar en este navegador. */
export function detectInstallPlatform(env: InstallEnvironment): InstallPlatform {
  if (env.standalone) return 'installed'
  const ua = env.userAgent
  // Navegadores dentro de apps: Facebook, Instagram, LINE, Twitter/X y el
  // WebView de Android (`; wv)`), que es lo que usa WhatsApp al abrir enlaces.
  if (/FBAN|FBAV|Instagram|Line\/|Twitter|; wv\)/.test(ua)) return 'in-app'
  const ios = /iPad|iPhone|iPod/.test(ua) || (env.platform === 'MacIntel' && env.maxTouchPoints > 1)
  if (ios) return /CriOS|FxiOS|EdgiOS|OPiOS/.test(ua) ? 'ios-other' : 'ios-safari'
  if (env.hasPrompt) return 'prompt'
  if (/Android/.test(ua)) return /Firefox/.test(ua) ? 'android-firefox' : 'android'
  if (/Firefox/.test(ua)) return 'firefox-desktop'
  if (/Macintosh/.test(ua) && /Safari/.test(ua) && !/Chrome|Chromium|Edg\//.test(ua)) {
    return 'mac-safari'
  }
  return 'desktop-chromium'
}

/** Dónde vale la pena invitar: se puede instalar desde este mismo navegador. */
function canInviteOn(platform: InstallPlatform): boolean {
  return platform !== 'installed' && platform !== 'firefox-desktop' && platform !== 'in-app'
}

// ---------------------------------------------------------------------------
// El evento de Chrome y el estado instalado
// ---------------------------------------------------------------------------

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

let deferred: BeforeInstallPromptEvent | null = null
let installedNow = false
let version = 0
const listeners = new Set<() => void>()

function emit(): void {
  version += 1
  for (const listener of listeners) listener()
}

function readStandalone(): boolean {
  if (typeof window === 'undefined') return false
  const nav = window.navigator as Navigator & { standalone?: boolean }
  if (nav.standalone === true) return true
  try {
    return window.matchMedia('(display-mode: standalone)').matches
  } catch {
    return false
  }
}

function readEnvironment(): InstallEnvironment {
  const nav = window.navigator
  return {
    userAgent: nav.userAgent,
    platform: nav.platform,
    maxTouchPoints: nav.maxTouchPoints ?? 0,
    standalone: installedNow || readStandalone(),
    hasPrompt: deferred !== null,
  }
}

if (typeof window !== 'undefined') {
  // Se escucha al importar el módulo (desde `main.tsx`): el evento puede llegar
  // antes de que React monte nada, y si nadie lo guarda se pierde.
  window.addEventListener('beforeinstallprompt', (event) => {
    // Sin `preventDefault`, Chrome en Android muestra además su propia barra
    // inferior: dos invitaciones a la vez.
    event.preventDefault()
    deferred = event as BeforeInstallPromptEvent
    emit()
  })
  window.addEventListener('appinstalled', () => {
    installedNow = true
    deferred = null
    emit()
  })
}

export type PromptOutcome = 'accepted' | 'dismissed' | 'unavailable'

/** Abre el instalador nativo, si este navegador lo ofreció. */
export async function promptInstall(): Promise<PromptOutcome> {
  const event = deferred
  if (!event) return 'unavailable'
  // El evento sirve una sola vez.
  deferred = null
  emit()
  await event.prompt()
  const { outcome } = await event.userChoice
  if (outcome === 'accepted') {
    installedNow = true
    emit()
  }
  return outcome
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

let cache: { version: number; platform: InstallPlatform } | null = null
function snapshot(): InstallPlatform {
  if (typeof window === 'undefined') return 'installed'
  if (!cache || cache.version !== version) {
    cache = { version, platform: detectInstallPlatform(readEnvironment()) }
  }
  return cache.platform
}

export function useInstallPlatform(): InstallPlatform {
  return useSyncExternalStore(subscribe, snapshot, () => 'installed' as const)
}

// ---------------------------------------------------------------------------
// Visitas e invitación
// ---------------------------------------------------------------------------

const VISITS_KEY = 'alertav:visitas'
const SESSION_KEY = 'alertav:visita-contada'
const DISMISS_KEY = 'alertav:instalar-no'

function storage(kind: 'local' | 'session'): Storage | null {
  try {
    if (typeof window === 'undefined') return null
    return kind === 'local' ? window.localStorage : window.sessionStorage
  } catch {
    return null
  }
}

/**
 * Cuenta esta visita, una vez por sesión del navegador (recargar no suma).
 * Devuelve el total, incluida ésta.
 */
export function countVisit(): number {
  const local = storage('local')
  const session = storage('session')
  try {
    const previous = Number(local?.getItem(VISITS_KEY) ?? '0') || 0
    if (session?.getItem(SESSION_KEY) === '1') return previous
    const next = previous + 1
    local?.setItem(VISITS_KEY, String(next))
    session?.setItem(SESSION_KEY, '1')
    return next
  } catch {
    return 1
  }
}

export function inviteDismissed(): boolean {
  try {
    return storage('local')?.getItem(DISMISS_KEY) === '1'
  } catch {
    return false
  }
}

export function dismissInstallInvite(): void {
  try {
    storage('local')?.setItem(DISMISS_KEY, '1')
  } catch {
    // Sin almacenamiento: se cierra mientras la página esté abierta.
  }
}

/** Desde qué visita se invita. */
export const INVITE_FROM_VISIT = 2

/** Decisión pura de la invitación. */
export function shouldInvite(platform: InstallPlatform, visits: number, dismissed: boolean): boolean {
  return canInviteOn(platform) && visits >= INVITE_FROM_VISIT && !dismissed
}

// ---------------------------------------------------------------------------
// La hoja de pasos: se abre desde el menú y desde la invitación
// ---------------------------------------------------------------------------

let sheetOpen = false
const sheetListeners = new Set<() => void>()

export function setInstallSheetOpen(open: boolean): void {
  if (sheetOpen === open) return
  sheetOpen = open
  for (const listener of sheetListeners) listener()
}

export function useInstallSheetOpen(): boolean {
  return useSyncExternalStore(
    (listener) => {
      sheetListeners.add(listener)
      return () => sheetListeners.delete(listener)
    },
    () => sheetOpen,
    () => false,
  )
}

/** «Instalar»: el instalador nativo si existe; si no, la hoja con los pasos. */
export async function startInstall(): Promise<void> {
  const outcome = await promptInstall()
  if (outcome === 'unavailable') setInstallSheetOpen(true)
}
