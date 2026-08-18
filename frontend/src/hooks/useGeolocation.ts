import { useCallback, useEffect, useRef, useState } from 'react'

export interface Coordinates {
  lat: number
  lon: number
  /** Radio de incertidumbre en metros que informa el dispositivo. */
  accuracyM: number
}

export type GeolocationStatus =
  | 'idle'
  | 'unsupported'
  | 'locating'
  | 'ready'
  | 'error'

export interface GeolocationState {
  status: GeolocationStatus
  coords: Coordinates | null
  /** Mensaje ya redactado para mostrarle a una persona, no el código crudo. */
  error: string | null
  /** `true` cuando el navegador denegó el permiso: reintentar no sirve. */
  denied: boolean
  request: () => void
  reset: () => void
}

/**
 * Traduce los códigos de `GeolocationPositionError` a algo accionable.
 *
 * "PERMISSION_DENIED" no le dice nada a nadie. Lo que hace falta saber es qué
 * botón tocar para arreglarlo, o si conviene dejar de intentar.
 */
function describe(error: GeolocationPositionError): string {
  switch (error.code) {
    case error.PERMISSION_DENIED:
      return 'Bloqueaste el acceso a tu ubicación. Habilítala en el candado de la barra de direcciones (o en los ajustes del sitio) y vuelve a intentar.'
    case error.POSITION_UNAVAILABLE:
      return 'Tu dispositivo no pudo determinar la ubicación. Si estás en interior o en una quebrada, sal a cielo abierto e intenta de nuevo.'
    case error.TIMEOUT:
      return 'El GPS tardó demasiado en responder. Vuelve a intentar.'
    default:
      return 'No se pudo obtener tu ubicación.'
  }
}

/**
 * Ubicación puntual del dispositivo, bajo demanda.
 *
 * Deliberadamente `getCurrentPosition` y no `watchPosition`: un reporte
 * describe un punto y un instante. Seguir al usuario mientras escribe gastaría
 * batería y haría que las coordenadas cambiaran bajo sus pies entre que
 * describe lo que ve y aprieta enviar.
 *
 * El seguimiento continuo ya existe en el mapa, en el `GeolocateControl`.
 */
export function useGeolocation(): GeolocationState {
  const [status, setStatus] = useState<GeolocationStatus>('idle')
  const [coords, setCoords] = useState<Coordinates | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [denied, setDenied] = useState(false)

  // El diálogo de permisos puede quedar abierto más que el modal. Si el usuario
  // cierra el modal y recién ahí acepta, el callback llegaría a un componente
  // desmontado: React 19 no revienta, pero deja estado zombi.
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const request = useCallback(() => {
    if (typeof navigator === 'undefined' || !('geolocation' in navigator)) {
      setStatus('unsupported')
      setError(
        'Este navegador no expone la ubicación. Abre AlertaV en Chrome, Safari o Firefox actualizados.',
      )
      return
    }

    // La API exige contexto seguro. Sin esto el fallo llega como un
    // PERMISSION_DENIED indistinguible de "el usuario dijo que no".
    if (!window.isSecureContext) {
      setStatus('unsupported')
      setError(
        'La ubicación solo funciona sobre HTTPS (o localhost). Esta página no está en un contexto seguro.',
      )
      return
    }

    setStatus('locating')
    setError(null)
    setDenied(false)

    navigator.geolocation.getCurrentPosition(
      (position) => {
        if (!mounted.current) return
        setCoords({
          lat: position.coords.latitude,
          lon: position.coords.longitude,
          accuracyM: position.coords.accuracy,
        })
        setStatus('ready')
      },
      (positionError) => {
        if (!mounted.current) return
        setDenied(positionError.code === positionError.PERMISSION_DENIED)
        setError(describe(positionError))
        setStatus('error')
      },
      {
        enableHighAccuracy: true,
        // 12 s: más que eso y la persona ya cerró la app. Un reporte con GPS
        // grueso llega igual al motor de correlación; uno que nunca se envía, no.
        timeout: 12_000,
        // Una lectura de hasta 30 s es indistinguible de una nueva para este
        // uso, y ahorra el arranque en frío del GPS.
        maximumAge: 30_000,
      },
    )
  }, [])

  const reset = useCallback(() => {
    setStatus('idle')
    setCoords(null)
    setError(null)
    setDenied(false)
  }, [])

  return { status, coords, error, denied, request, reset }
}
