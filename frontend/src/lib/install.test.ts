import { afterEach, describe, expect, it } from 'vitest'
import {
  INVITE_FROM_VISIT,
  countVisit,
  detectInstallPlatform,
  dismissInstallInvite,
  inviteDismissed,
  shouldInvite,
  type InstallEnvironment,
} from './install'

const IPHONE_SAFARI =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1'
const IPHONE_CHROME =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1'
const ANDROID_CHROME =
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36'
const ANDROID_WEBVIEW =
  'Mozilla/5.0 (Linux; Android 14; Pixel 8; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0 Mobile Safari/537.36'
const ANDROID_FIREFOX = 'Mozilla/5.0 (Android 14; Mobile; rv:130.0) Gecko/130.0 Firefox/130.0'
const WIN_CHROME =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
const WIN_FIREFOX = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0'
const MAC_SAFARI =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15'
const INSTAGRAM = `${IPHONE_SAFARI} Instagram 300.0`

const env = (userAgent: string, over: Partial<InstallEnvironment> = {}): InstallEnvironment => ({
  userAgent,
  platform: /Mac/.test(userAgent) ? 'MacIntel' : 'Win32',
  maxTouchPoints: /iPhone|Android/.test(userAgent) ? 5 : 0,
  standalone: false,
  hasPrompt: false,
  ...over,
})

describe('qué navegador es (§Q)', () => {
  it('instalada: nada que mostrar', () => {
    expect(detectInstallPlatform(env(IPHONE_SAFARI, { standalone: true }))).toBe('installed')
  })

  it('Chrome o Edge con el aviso guardado: botón propio', () => {
    expect(detectInstallPlatform(env(ANDROID_CHROME, { hasPrompt: true }))).toBe('prompt')
    expect(detectInstallPlatform(env(WIN_CHROME, { hasPrompt: true }))).toBe('prompt')
  })

  it('iPhone: Safari o un navegador de otro, cada uno con sus pasos', () => {
    expect(detectInstallPlatform(env(IPHONE_SAFARI))).toBe('ios-safari')
    expect(detectInstallPlatform(env(IPHONE_CHROME))).toBe('ios-other')
  })

  it('el iPad se presenta como Mac con pantalla táctil', () => {
    expect(detectInstallPlatform(env(MAC_SAFARI, { maxTouchPoints: 5 }))).toBe('ios-safari')
  })

  it('dentro de otra app no se puede instalar', () => {
    expect(detectInstallPlatform(env(INSTAGRAM))).toBe('in-app')
    expect(detectInstallPlatform(env(ANDROID_WEBVIEW))).toBe('in-app')
  })

  it('Android y escritorio sin el aviso todavía: pasos manuales', () => {
    expect(detectInstallPlatform(env(ANDROID_CHROME))).toBe('android')
    expect(detectInstallPlatform(env(ANDROID_FIREFOX))).toBe('android-firefox')
    expect(detectInstallPlatform(env(WIN_CHROME))).toBe('desktop-chromium')
    expect(detectInstallPlatform(env(MAC_SAFARI))).toBe('mac-safari')
    expect(detectInstallPlatform(env(WIN_FIREFOX))).toBe('firefox-desktop')
  })
})

describe('la invitación', () => {
  afterEach(() => {
    localStorage.clear()
    sessionStorage.clear()
  })

  it('aparece desde la segunda visita, nunca en la primera', () => {
    expect(INVITE_FROM_VISIT).toBe(2)
    expect(shouldInvite('ios-safari', 1, false)).toBe(false)
    expect(shouldInvite('ios-safari', 2, false)).toBe(true)
  })

  it('no aparece instalada, cerrada, ni donde no se puede instalar', () => {
    expect(shouldInvite('installed', 5, false)).toBe(false)
    expect(shouldInvite('prompt', 5, true)).toBe(false)
    expect(shouldInvite('firefox-desktop', 5, false)).toBe(false)
    expect(shouldInvite('in-app', 5, false)).toBe(false)
  })

  it('una visita por sesión: recargar no suma', () => {
    expect(countVisit()).toBe(1)
    expect(countVisit()).toBe(1)
    sessionStorage.clear() // otra sesión del navegador
    expect(countVisit()).toBe(2)
  })

  it('«✕» se recuerda', () => {
    expect(inviteDismissed()).toBe(false)
    dismissInstallInvite()
    expect(inviteDismissed()).toBe(true)
  })
})
