import { useCallback, useEffect, useState } from 'react'
import { parseDeepLink, type PushDeepLink } from '@/lib/push'

/**
 * Destino pendiente de una notificación tocada.
 *
 * Llega por dos caminos, según si la app estaba abierta:
 *
 * - **Cerrada:** el service worker la abre en `/?incidente=INC-…`. Se lee la URL
 *   al montar y se limpia enseguida con `replaceState`, para que recargar la
 *   página no vuelva a saltar al mismo incidente una hora después.
 * - **Abierta:** el service worker le manda un `postMessage` en vez de recargar
 *   (ver `public/push-sw.js`), y la app salta sin perder su estado.
 *
 * El hook sólo entrega el destino; qué hacer con él —encender la capa, volar,
 * abrir la ficha— lo decide `App`, que es quien tiene esas piezas.
 */
export function useNotificationDeepLink(): {
  link: PushDeepLink | null
  clear: () => void
} {
  const [link, setLink] = useState<PushDeepLink | null>(() =>
    typeof window === 'undefined' ? null : parseDeepLink(window.location.href),
  )

  useEffect(() => {
    const url = new URL(window.location.href)
    if (url.searchParams.has('incidente') || url.searchParams.has('sismo')) {
      for (const key of ['incidente', 'sismo', 'lat', 'lon']) url.searchParams.delete(key)
      window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash)
    }
  }, [])

  useEffect(() => {
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { type?: unknown; url?: unknown } | null
      if (data?.type !== 'alertav:navigate' || typeof data.url !== 'string') return
      const next = parseDeepLink(data.url, window.location.origin)
      if (next) setLink(next)
    }
    navigator.serviceWorker.addEventListener('message', onMessage)
    return () => navigator.serviceWorker.removeEventListener('message', onMessage)
  }, [])

  const clear = useCallback(() => setLink(null), [])
  return { link, clear }
}
