import { afterEach, describe, expect, it } from 'vitest'
import {
  RESYNC_MAX_AGE_MS,
  RESYNC_MIN_INTERVAL_MS,
  clearPushMemo,
  detectPushSupport,
  loadPushMemo,
  parseDeepLink,
  sameServerKey,
  savePushMemo,
  shouldResync,
  urlBase64ToUint8Array,
  usgsIdOf,
  type PushEnvironment,
  type PushMemo,
} from './push'

const ANDROID_CHROME: PushEnvironment = {
  isSecureContext: true,
  userAgent:
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Mobile Safari/537.36',
  platform: 'Linux armv8l',
  maxTouchPoints: 5,
  standalone: false,
  hasServiceWorker: true,
  hasPushManager: true,
  hasNotification: true,
}

const IPHONE_SAFARI: PushEnvironment = {
  ...ANDROID_CHROME,
  userAgent:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1',
  platform: 'iPhone',
  // Safari no expone PushManager fuera de la app instalada.
  hasPushManager: false,
}

describe('detectPushSupport', () => {
  it('Chrome en Android puede suscribirse sin instalar la app', () => {
    expect(detectPushSupport(ANDROID_CHROME)).toBe('supported')
  })

  it('en iPhone pide instalar la app en vez de decir «no soportado»', () => {
    expect(detectPushSupport(IPHONE_SAFARI)).toBe('ios-install')
  })

  it('en iPhone con la app instalada funciona', () => {
    expect(
      detectPushSupport({ ...IPHONE_SAFARI, standalone: true, hasPushManager: true }),
    ).toBe('supported')
  })

  it('reconoce el iPad que se presenta como Mac', () => {
    expect(
      detectPushSupport({
        ...ANDROID_CHROME,
        userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15',
        platform: 'MacIntel',
        maxTouchPoints: 5,
        hasPushManager: false,
      }),
    ).toBe('ios-install')
  })

  it('sin HTTPS no hay nada que hacer', () => {
    expect(detectPushSupport({ ...ANDROID_CHROME, isSecureContext: false })).toBe('insecure')
  })

  it('un WebView sin la API no está soportado', () => {
    expect(detectPushSupport({ ...ANDROID_CHROME, hasPushManager: false })).toBe('unsupported')
  })
})

describe('claves', () => {
  // La clave pública del ejemplo del RFC 8291.
  const KEY =
    'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8'

  it('decodifica base64url sin relleno a los 65 bytes del punto', () => {
    const bytes = urlBase64ToUint8Array(KEY)
    expect(bytes).toHaveLength(65)
    expect(bytes[0]).toBe(0x04)
  })

  it('compara la clave de una suscripción existente', () => {
    const bytes = urlBase64ToUint8Array(KEY)
    expect(sameServerKey(bytes.slice().buffer, bytes)).toBe(true)
    const other = bytes.slice()
    other[10] = other[10]! ^ 0xff
    expect(sameServerKey(other.buffer, bytes)).toBe(false)
    expect(sameServerKey(null, bytes)).toBe(false)
  })
})

describe('shouldResync', () => {
  const NOW = 1_790_000_000_000
  const memo: PushMemo = {
    syncedAt: NOW - RESYNC_MIN_INTERVAL_MS - 1,
    lat: -33.025,
    lon: -71.551,
    notifyIncidents: true,
    notifySeismic: true,
  }

  it('sin registro previo, siempre', () => {
    expect(shouldResync(null, null, NOW)).toBe(true)
  })

  it('no por moverse unos metros', () => {
    expect(shouldResync(memo, { lat: -33.026, lon: -71.552 }, NOW)).toBe(false)
  })

  it('sí al moverse más de 250 m', () => {
    expect(shouldResync(memo, { lat: -33.03, lon: -71.551 }, NOW)).toBe(true)
  })

  it('no dos veces seguidas en diez minutos, aunque se haya movido', () => {
    expect(
      shouldResync({ ...memo, syncedAt: NOW - 60_000 }, { lat: -33.1, lon: -71.6 }, NOW),
    ).toBe(false)
  })

  it('cada 12 horas aunque no haya ubicación nueva', () => {
    expect(shouldResync({ ...memo, syncedAt: NOW - RESYNC_MAX_AGE_MS }, null, NOW)).toBe(true)
  })
})

describe('memoria local', () => {
  afterEach(() => clearPushMemo())

  it('guarda y recupera', () => {
    const memo: PushMemo = {
      syncedAt: 1,
      lat: -33,
      lon: -71.5,
      notifyIncidents: false,
      notifySeismic: true,
    }
    savePushMemo(memo)
    expect(loadPushMemo()).toEqual(memo)
  })

  it('ignora basura', () => {
    localStorage.setItem('alertav:push', '{"lat":"x"}')
    expect(loadPushMemo()).toBeNull()
    localStorage.setItem('alertav:push', 'no-es-json')
    expect(loadPushMemo()).toBeNull()
  })
})

describe('parseDeepLink', () => {
  it('lee un incidente', () => {
    expect(parseDeepLink('/?incidente=INC-2026-00142')).toEqual({
      kind: 'incident',
      code: 'INC-2026-00142',
    })
  })

  it('lee un sismo con su epicentro', () => {
    expect(parseDeepLink('https://alertav.vercel.app/?sismo=csn%3A379889&lat=-33.0200&lon=-71.9000')).toEqual({
      kind: 'seismic',
      key: 'csn:379889',
      lat: -33.02,
      lon: -71.9,
    })
  })

  it('descarta folios inventados y sismos sin coordenadas', () => {
    expect(parseDeepLink('/?incidente=<script>')).toBeNull()
    expect(parseDeepLink('/?sismo=csn:1')).toBeNull()
    expect(parseDeepLink('/?sismo=csn:1&lat=abc&lon=1')).toBeNull()
    expect(parseDeepLink('/')).toBeNull()
  })

  it('usgs_id de la capa de sismos', () => {
    expect(usgsIdOf('usgs:us6000tlm3')).toBe('us6000tlm3')
    expect(usgsIdOf('csn:379889')).toBeNull()
  })
})
