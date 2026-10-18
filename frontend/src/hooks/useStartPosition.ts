import { useEffect, useRef, type RefObject } from 'react'
import type { MapRef } from 'react-map-gl/maplibre'
import { NEARBY_ZOOM, shouldGoToPosition, type StartView } from '@/lib/initialView'
import { geolocationGranted, reportPosition } from '@/lib/positionStore'

/**
 * Al abrir la app, si el permiso de ubicación ya está concedido, lleva el mapa
 * a donde está la persona (ver `lib/initialView`). Una sola vez por apertura.
 *
 * Nunca pide el permiso: sin él el mapa se queda en Casa o en la región. Y no
 * le quita el mapa a nadie: si en lo que tarda el GPS la cámara se movió —un
 * arrastre, un incidente tocado—, la lectura sólo pone al día los avisos.
 */
export function useStartPosition(
  mapRef: RefObject<MapRef | null>,
  start: StartView,
  skip: boolean,
): void {
  // Sólo cuenta lo que había al montar: un enlace que llega después no
  // vuelve a disparar esto.
  const initial = useRef({ start, skip })

  useEffect(() => {
    const { start: view, skip: skipped } = initial.current
    if (skipped || typeof navigator === 'undefined' || !navigator.geolocation) return
    let cancelled = false
    const startedAt = Date.now()

    void geolocationGranted().then((granted) => {
      if (!granted || cancelled) return
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const position = { lat: pos.coords.latitude, lon: pos.coords.longitude }
          reportPosition({
            ...position,
            accuracyM: Number.isFinite(pos.coords.accuracy) ? pos.coords.accuracy : null,
            at: pos.timestamp || Date.now(),
          })
          const map = mapRef.current
          if (cancelled || !map) return
          const center = map.getCenter()
          const untouched =
            !map.isMoving() &&
            Math.abs(map.getZoom() - view.zoom) < 0.01 &&
            Math.abs(center.lng - view.longitude) < 1e-4 &&
            Math.abs(center.lat - view.latitude) < 1e-4
          const go = shouldGoToPosition({
            position,
            untouched,
            elapsedMs: Date.now() - startedAt,
            start: view,
          })
          if (go) {
            map.easeTo({
              center: [position.lon, position.lat],
              zoom: NEARBY_ZOOM,
              duration: 900,
              essential: true,
            })
          }
        },
        () => {
          // Sin lectura (GPS apagado, interior): el mapa se queda donde está.
        },
        { enableHighAccuracy: false, timeout: 6_000, maximumAge: 10 * 60_000 },
      )
    })

    return () => {
      cancelled = true
    }
  }, [mapRef])
}
