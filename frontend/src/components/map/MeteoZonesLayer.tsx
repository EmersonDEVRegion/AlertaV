import { useEffect, useMemo, useRef } from 'react'
import { Layer, Source, useMap } from 'react-map-gl/maplibre'
import { boundsOfWarning } from '@/api/meteoZones'
import { useMeteoZones } from '@/hooks/useMeteoZones'
import type { Theme } from '@/hooks/useTheme'
import { fitPadding, focusOffset } from '@/lib/cameraOffset'
import {
  METEO_ZONES_BEFORE_ID,
  METEO_ZONES_SOURCE_ID,
  METEO_ZONE_LAYER_IDS,
  meteoZoneSpecs,
} from './meteoZonesLayers'
import { useLayerReanchor } from './useLayerReanchor'

const ANCHORS = [METEO_ZONES_BEFORE_ID] as const

/**
 * Zonas de pronóstico de la DMC con avisos vigentes («Avisos DMC» en Capas).
 *
 * Lee su estado del store (`meteoZonesStore`) y sus datos de la API por su
 * cuenta: así el azulejo de «Capas» y el «Ver en el mapa» de la franja la
 * encienden sin pasar por `App` ni agregar props al mapa.
 *
 * Igual que las otras capas de referencia: no se monta hasta el primer
 * encendido y después sólo alterna `visibility`.
 */
export function MeteoZonesLayer({ theme }: { theme: Theme }) {
  const { current: map } = useMap()
  const instance = map?.getMap() ?? null
  const { enabled, hasMounted, focus, data } = useMeteoZones()
  useLayerReanchor(instance, METEO_ZONE_LAYER_IDS, ANCHORS)

  const focusId = focus?.id ?? null
  const { fill, line, strong, focus: focusLine } = useMemo(
    () => meteoZoneSpecs(theme, enabled, focusId),
    [theme, enabled, focusId],
  )

  // «Ver en el mapa»: encuadra las zonas del aviso UNA vez por toque, cuando
  // ya están los datos (el toque puede llegar antes que la descarga). El
  // `nonce` hace que un segundo toque vuelva a encuadrar; la ref, que el
  // sondeo de cada 5 minutos no mueva la cámara mientras se mira otra cosa.
  const nonce = focus?.nonce ?? 0
  const framed = useRef(0)
  useEffect(() => {
    if (!instance || !focusId || framed.current === nonce) return
    const bounds = boundsOfWarning(data, focusId)
    if (!bounds) return
    framed.current = nonce
    const padding = fitPadding()
    // La región mide ~220 km de norte a sur: en el teléfono, con la hoja
    // abierta, no cabe sobre el zoom mínimo del mapa, y `fitBounds` la deja
    // descentrada contra ese tope. En ese caso se centra en la caja al zoom
    // mínimo, corrida como cualquier foco para quedar sobre la hoja.
    const camera = instance.cameraForBounds(bounds, { padding, maxZoom: 10 })
    if (camera?.zoom !== undefined && camera.zoom >= instance.getMinZoom()) {
      instance.fitBounds(bounds, { padding, maxZoom: 10, duration: 800 })
    } else {
      const [west, south, east, north] = bounds
      instance.easeTo({
        center: [(west + east) / 2, (south + north) / 2],
        zoom: instance.getMinZoom(),
        offset: focusOffset(),
        duration: 800,
      })
    }
  }, [instance, focusId, nonce, data])

  if (!hasMounted) return null
  return (
    <Source id={METEO_ZONES_SOURCE_ID} type="geojson" data={data}>
      <Layer beforeId={METEO_ZONES_BEFORE_ID} {...fill} />
      <Layer beforeId={METEO_ZONES_BEFORE_ID} {...line} />
      <Layer beforeId={METEO_ZONES_BEFORE_ID} {...strong} />
      <Layer beforeId={METEO_ZONES_BEFORE_ID} {...focusLine} />
    </Source>
  )
}
