import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '@/api/client'
import {
  fetchPushStatus,
  registerPushSubscription,
  sendPushProbe,
  unregisterPushSubscription,
  type PushServerStatus,
} from '@/api/push'
import {
  clearPushMemo,
  detectPushSupport,
  loadPushMemo,
  placesKey,
  readPushEnvironment,
  sameServerKey,
  savePushMemo,
  shouldResync,
  urlBase64ToUint8Array,
  type PushMemo,
  type PushSupport,
} from '@/lib/push'
import { getPlaces, usePlaces } from '@/lib/placesStore'

/**
 * Estado de los avisos en este teléfono.
 *
 * - `checking`: todavía no se sabe (primer render, o esperando al servidor).
 * - `unavailable`: no hay forma de activarlos acá; `message` dice por qué.
 * - `ios-install`: iPhone fuera de la app instalada.
 * - `denied`: la persona bloqueó las notificaciones para el sitio.
 * - `off` / `on`: lo obvio.
 * - `working`: pidiendo permiso, ubicación o hablando con el servidor.
 */
type PushPhase =
  | 'checking'
  | 'unavailable'
  | 'ios-install'
  | 'denied'
  | 'off'
  | 'working'
  | 'on'

interface PushPreferences {
  notifyIncidents: boolean
  notifySeismic: boolean
  /** Radios elegidos por categoría (sólo los tocados). Ver `lib/radios.ts`. */
  radios: Record<string, number>
}

export interface PushNotificationsState {
  phase: PushPhase
  /** Qué está haciendo mientras `phase === 'working'`. */
  step: 'permission' | 'location' | 'subscribing' | 'unsubscribing' | 'saving' | null
  /** Motivo de `unavailable`, o el último error, ya redactado para una persona. */
  message: string | null
  server: PushServerStatus | undefined
  preferences: PushPreferences
  /**
   * Cuándo se obtuvo la ubicación con la que el servidor mide distancias, en
   * ms. No es cuándo se sincronizó: puede ser de hace días si la app no se
   * abrió con permiso de ubicación.
   */
  locatedAt: number | null
  probeResult: string | null
  enable: () => Promise<void>
  disable: () => Promise<void>
  setPreferences: (next: PushPreferences) => Promise<void>
  refreshLocation: () => Promise<void>
  sendProbe: () => Promise<void>
}

const DEFAULT_PREFERENCES: PushPreferences = {
  notifyIncidents: true,
  notifySeismic: true,
  radios: {},
}

const SW_READY_TIMEOUT_MS = 10_000

class PushFlowError extends Error {}

/** `navigator.serviceWorker.ready` nunca resuelve si no hay service worker. */
async function serviceWorkerReady(): Promise<ServiceWorkerRegistration> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () =>
        reject(
          new PushFlowError(
            import.meta.env.DEV
              ? 'El service worker no está activo en desarrollo. Prueba los avisos con `npm run build && npm run preview`.'
              : 'La app todavía se está instalando. Recarga la página e intenta de nuevo.',
          ),
        ),
      SW_READY_TIMEOUT_MS,
    )
  })
  try {
    return await Promise.race([navigator.serviceWorker.ready, timeout])
  } finally {
    clearTimeout(timer)
  }
}

interface Position {
  lat: number
  lon: number
  accuracyM: number | null
  /** Cuándo se obtuvo, en ms. */
  at: number
}

/**
 * Ubicación para los avisos: precisión baja a propósito. Un radio de 5 km no
 * necesita GPS fino, y la ubicación por red responde en un segundo en interior,
 * donde el GPS puede tardar un minuto o no responder nunca.
 */
function locate(): Promise<Position> {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject(new PushFlowError('Este navegador no entrega la ubicación.'))
      return
    }
    navigator.geolocation.getCurrentPosition(
      (position) =>
        resolve({
          lat: position.coords.latitude,
          lon: position.coords.longitude,
          accuracyM: Number.isFinite(position.coords.accuracy)
            ? position.coords.accuracy
            : null,
          // Con `maximumAge` el navegador puede devolver una lectura de hace
          // unos minutos: su hora es la que vale, no la de ahora.
          at: position.timestamp || Date.now(),
        }),
      (error) =>
        reject(
          new PushFlowError(
            error.code === error.PERMISSION_DENIED
              ? 'Sin tu ubicación no podemos saber qué emergencias están a menos de 5 km. Permite la ubicación para AlertaV (en el candado de la barra de direcciones) y vuelve a intentar.'
              : 'No pudimos obtener tu ubicación. Intenta de nuevo en un momento.',
          ),
        ),
      { enableHighAccuracy: false, timeout: 15_000, maximumAge: 10 * 60_000 },
    )
  })
}

/** ¿El permiso de ubicación ya está concedido? Sin preguntar. */
async function geolocationGranted(): Promise<boolean> {
  try {
    const status = await navigator.permissions.query({ name: 'geolocation' })
    return status.state === 'granted'
  } catch {
    return false
  }
}

function readable(error: unknown): string {
  if (error instanceof PushFlowError) return error.message
  if (error instanceof ApiError) {
    if (error.status === 0) return 'No hay conexión con el servidor. Intenta cuando tengas señal.'
    if (error.status === 422) {
      const body = error.body as { error?: { message?: string } } | undefined
      return body?.error?.message ?? 'El servidor rechazó la suscripción.'
    }
    if (error.status === 503) return 'Los avisos no están configurados en el servidor todavía.'
    if (error.status === 429) return 'Espera un minuto antes de volver a probar.'
  }
  if (error instanceof DOMException && error.name === 'NotAllowedError') {
    return 'El navegador no permitió activar los avisos.'
  }
  if (error instanceof DOMException && error.name === 'AbortError') {
    // Lo que devuelve `pushManager.subscribe` cuando el navegador no logra
    // registrarse en su propio servicio de push. El caso más común es la
    // ventana de incógnito, donde Chrome no ofrece push y no deja detectarlo.
    return 'El navegador no pudo registrarse en su servicio de notificaciones. Si estás en una ventana de incógnito, abre AlertaV en una ventana normal.'
  }
  return 'No se pudieron activar los avisos. Intenta de nuevo.'
}

/**
 * Avisos push: permiso, suscripción y última ubicación conocida.
 *
 * # La ubicación
 *
 * Con la app cerrada el navegador no entrega la ubicación, así que el servidor
 * calcula las distancias contra la última que recibió. Por eso este hook la
 * reenvía cada vez que la app se abre —si ya hay permiso y el teléfono se movió
 * más de 250 m, o si pasaron 12 horas— y sin volver a preguntar nada: al abrir
 * la app sólo se consulta el GPS si el permiso ya estaba concedido.
 *
 * # Por qué no se pide permiso al cargar
 *
 * Chrome y Safari castigan a los sitios que piden permisos sin un gesto de la
 * persona (el diálogo deja de mostrarse). El permiso sólo se pide dentro de
 * `enable`, que se llama desde un botón.
 */
export function usePushNotifications(): PushNotificationsState {
  const [support] = useState<PushSupport>(() =>
    typeof window === 'undefined' ? 'unsupported' : detectPushSupport(readPushEnvironment()),
  )
  const [phase, setPhase] = useState<PushPhase>('checking')
  const [step, setStep] = useState<PushNotificationsState['step']>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [memo, setMemo] = useState<PushMemo | null>(() => loadPushMemo())
  const [probeResult, setProbeResult] = useState<string | null>(null)

  const status = useQuery({
    queryKey: ['push', 'status'],
    queryFn: ({ signal }) => fetchPushStatus(signal),
    enabled: support === 'supported',
    staleTime: 60 * 60_000,
    refetchOnWindowFocus: false,
  })
  const server = status.data
  const publicKey = server?.public_key ?? null

  const busy = useRef(false)

  /**
   * Registra en el servidor la suscripción actual con una ubicación y los
   * lugares guardados de este teléfono.
   */
  const sync = useCallback(
    async (subscription: PushSubscription, position: Position, preferences: PushPreferences) => {
      const places = getPlaces().map(({ name, lat, lon }) => ({ name, lat, lon }))
      const saved = await registerPushSubscription({
        subscription: subscription.toJSON(),
        lat: position.lat,
        lon: position.lon,
        accuracy_m: position.accuracyM,
        notify_incidents: preferences.notifyIncidents,
        notify_seismic: preferences.notifySeismic,
        located_at: new Date(position.at).toISOString(),
        places,
        // Sin radios elegidos no se mandan: el servidor conserva los suyos.
        ...(Object.keys(preferences.radios).length > 0 ? { radios: preferences.radios } : {}),
      })
      const next: PushMemo = {
        syncedAt: Date.now(),
        locatedAt: position.at,
        lat: saved.lat,
        lon: saved.lon,
        notifyIncidents: saved.notify_incidents,
        notifySeismic: saved.notify_seismic,
        radios: preferences.radios,
        placesKey: placesKey(places),
      }
      savePushMemo(next)
      setMemo(next)
    },
    [],
  )

  // --- Estado inicial y resincronización silenciosa al abrir -----------------
  useEffect(() => {
    if (support === 'ios-install') {
      setPhase('ios-install')
      return
    }
    if (support === 'insecure') {
      setPhase('unavailable')
      setMessage('Los avisos necesitan que AlertaV se abra por HTTPS.')
      return
    }
    if (support === 'unsupported') {
      setPhase('unavailable')
      setMessage(
        'Este navegador no admite avisos. Abre AlertaV en Chrome, Edge, Firefox o Safari actualizados.',
      )
      return
    }
    if (status.isError) {
      setPhase('unavailable')
      setMessage('No se pudo consultar el servidor de avisos. Intenta más tarde.')
      return
    }
    if (!server) return
    if (!publicKey) {
      setPhase('unavailable')
      setMessage(server.reason ?? 'Los avisos no están configurados en el servidor todavía.')
      return
    }
    if (Notification.permission === 'denied') {
      setPhase('denied')
      return
    }

    let cancelled = false
    void (async () => {
      try {
        const registration = await serviceWorkerReady()
        const existing = await registration.pushManager.getSubscription()
        if (cancelled) return
        if (!existing || Notification.permission !== 'granted') {
          setPhase('off')
          return
        }
        if (!sameServerKey(existing.options.applicationServerKey, urlBase64ToUint8Array(publicKey))) {
          // Clave rotada en el servidor: esta suscripción ya no sirve. Se
          // descarta y se ofrece activar de nuevo.
          await existing.unsubscribe().catch(() => undefined)
          clearPushMemo()
          setMemo(null)
          setPhase('off')
          setMessage('Los avisos se reiniciaron en el servidor. Actívalos de nuevo.')
          return
        }
        setPhase('on')

        const current = loadPushMemo()
        const preferences = current ?? DEFAULT_PREFERENCES
        let position: Position | null = null
        if (await geolocationGranted()) {
          position = await locate().catch(() => null)
        }
        if (cancelled || !shouldResync(current, position, Date.now())) return
        const target =
          position ?? (current ? { ...current, accuracyM: null, at: current.locatedAt } : null)
        if (target) await sync(existing, target, preferences)
      } catch {
        // La resincronización es un extra: si falla, los avisos siguen
        // llegando a la última ubicación conocida.
        if (!cancelled) setPhase((previous) => (previous === 'checking' ? 'off' : previous))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [support, server, publicKey, status.isError, sync])

  // --- Lugares guardados --------------------------------------------------------
  /*
   * Guardar o borrar «Casa» con los avisos activos se lo cuenta al servidor
   * enseguida, con la ubicación que ya tenía (y su hora): no hace falta el GPS
   * para eso. Si falla, se reintenta la próxima vez que cambie algo o que se
   * abra la app (el memo sigue con la huella vieja).
   */
  const places = usePlaces()
  useEffect(() => {
    if (phase !== 'on' || !memo || placesKey(places) === memo.placesKey) return
    let cancelled = false
    void (async () => {
      try {
        const registration = await serviceWorkerReady()
        const subscription = await registration.pushManager.getSubscription()
        if (cancelled || !subscription) return
        await sync(
          subscription,
          { lat: memo.lat, lon: memo.lon, accuracyM: null, at: memo.locatedAt },
          memo,
        )
      } catch {
        // Ver arriba: se reintenta.
      }
    })()
    return () => {
      cancelled = true
    }
  }, [phase, memo, places, sync])

  // --- Acciones ----------------------------------------------------------------

  const run = useCallback(async (action: () => Promise<void>) => {
    if (busy.current) return
    busy.current = true
    setMessage(null)
    setProbeResult(null)
    try {
      await action()
    } finally {
      busy.current = false
      setStep(null)
    }
  }, [])

  const enable = useCallback(
    () =>
      run(async () => {
        if (!publicKey) return
        setPhase('working')
        try {
          setStep('permission')
          const permission =
            Notification.permission === 'granted'
              ? 'granted'
              : await Notification.requestPermission()
          if (permission !== 'granted') {
            setPhase(permission === 'denied' ? 'denied' : 'off')
            return
          }

          setStep('location')
          const position = await locate()

          setStep('subscribing')
          const registration = await serviceWorkerReady()
          const key = urlBase64ToUint8Array(publicKey)
          let subscription = await registration.pushManager.getSubscription()
          if (subscription && !sameServerKey(subscription.options.applicationServerKey, key)) {
            await subscription.unsubscribe()
            subscription = null
          }
          subscription ??= await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: key,
          })

          await sync(subscription, position, memo ?? DEFAULT_PREFERENCES)
          setPhase('on')
        } catch (error) {
          setMessage(readable(error))
          setPhase(Notification.permission === 'denied' ? 'denied' : 'off')
        }
      }),
    [run, publicKey, sync, memo],
  )

  const disable = useCallback(
    () =>
      run(async () => {
        setPhase('working')
        setStep('unsubscribing')
        try {
          const registration = await serviceWorkerReady()
          const subscription = await registration.pushManager.getSubscription()
          if (subscription) {
            // Primero el servidor: si la baja local ocurre y la del servidor
            // falla, quedaría una fila con ubicación que nadie puede borrar.
            await unregisterPushSubscription(subscription.endpoint)
            await subscription.unsubscribe()
          }
          clearPushMemo()
          setMemo(null)
          setPhase('off')
        } catch (error) {
          setMessage(readable(error))
          setPhase('on')
        }
      }),
    [run],
  )

  const setPreferences = useCallback(
    (next: PushPreferences) =>
      run(async () => {
        if (!memo) return
        setStep('saving')
        const previous = memo
        // Optimista: el interruptor responde al instante.
        setMemo({ ...memo, ...next })
        try {
          const registration = await serviceWorkerReady()
          const subscription = await registration.pushManager.getSubscription()
          if (!subscription) {
            setPhase('off')
            return
          }
          await sync(
            subscription,
            { lat: previous.lat, lon: previous.lon, accuracyM: null, at: previous.locatedAt },
            next,
          )
        } catch (error) {
          setMemo(previous)
          setMessage(readable(error))
        }
      }),
    [run, memo, sync],
  )

  const refreshLocation = useCallback(
    () =>
      run(async () => {
        setStep('location')
        try {
          const position = await locate()
          const registration = await serviceWorkerReady()
          const subscription = await registration.pushManager.getSubscription()
          if (!subscription) {
            setPhase('off')
            return
          }
          setStep('saving')
          await sync(subscription, position, memo ?? DEFAULT_PREFERENCES)
        } catch (error) {
          setMessage(readable(error))
        }
      }),
    [run, memo, sync],
  )

  const sendProbe = useCallback(
    () =>
      run(async () => {
        try {
          const registration = await serviceWorkerReady()
          const subscription = await registration.pushManager.getSubscription()
          if (!subscription) {
            setPhase('off')
            return
          }
          const result = await sendPushProbe(subscription.endpoint)
          setProbeResult(result.detail)
        } catch (error) {
          setMessage(readable(error))
        }
      }),
    [run],
  )

  return {
    phase,
    step,
    message,
    server,
    preferences: memo
      ? {
          notifyIncidents: memo.notifyIncidents,
          notifySeismic: memo.notifySeismic,
          radios: memo.radios,
        }
      : DEFAULT_PREFERENCES,
    locatedAt: memo?.locatedAt ?? null,
    probeResult,
    enable,
    disable,
    setPreferences,
    refreshLocation,
    sendProbe,
  }
}
