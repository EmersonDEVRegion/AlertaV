import { useEffect, useMemo, useRef } from 'react'
import { Layer, Source, useMap } from 'react-map-gl/maplibre'
import type { Feature, FeatureCollection } from 'geojson'
import {
  boundsOfTransito,
  type TransitoGeometry,
  type TransitoProperties,
} from '@/api/transito'
import { useNow } from '@/hooks/useNow'
import type { Theme } from '@/hooks/useTheme'
import { useTransito } from '@/hooks/useTransito'
import { fitPadding, focusOffset } from '@/lib/cameraOffset'
import { useTransitoState } from '@/lib/transitoStore'
import {
  TRANSITO_BEFORE_ID,
  TRANSITO_LAYER_IDS,
  TRANSITO_SOURCE_ID,
  transitoSpecs,
} from './transitoLayers'
import { useLayerReanchor } from './useLayerReanchor'

const ANCHORS = [TRANSITO_BEFORE_ID] as const

/** Cerca de una calle: el tramo se tiene que leer, no sólo verse. */
const FOCO_ZOOM_MAX = 16

/**
 * El tránsito del MTT sobre la calle («Tránsito» en Capas).
 *
 * Como `MeteoZonesLayer`: lee su estado del store y sus datos por su cuenta,
 * así el azulejo, la lista de «Ahora» y el enlace de una notificación la
 * encienden sin pasar por `App`. No se monta hasta el primer encendido y
 * después sólo alterna `visibility`.
 */
export function TransitoLayer({ theme }: { theme: Theme }) {
  const { current: map } = useMap()
  const instance = map?.getMap() ?? null
  const { enabled, hasMounted, ocultos, focus } = useTransitoState()
  const now = useNow(60_000)
  const { data: vigentes } = useTransito(now)
  // Lo que no pudimos ubicar va a la lista, no al mapa.
  const data = useMemo(
    (): FeatureCollection<TransitoGeometry, TransitoProperties> => ({
      type: 'FeatureCollection',
      features: vigentes.features.filter(
        (f): f is Feature<TransitoGeometry, TransitoProperties> => f.geometry !== null,
      ),
    }),
    [vigentes],
  )
  useLayerReanchor(instance, TRANSITO_LAYER_IDS, ANCHORS)

  const focusId = focus?.id ?? null
  const specs = useMemo(
    () => transitoSpecs(theme, enabled, ocultos, focusId),
    [theme, enabled, ocultos, focusId],
  )

  // «Ver en el mapa»: encuadra el tramo UNA vez por toque, cuando ya está el
  // dato. El `nonce` hace que un segundo toque vuelva a encuadrar; la ref, que
  // el sondeo no mueva la cámara mientras se mira otra cosa.
  const nonce = focus?.nonce ?? 0
  const framed = useRef(0)
  useEffect(() => {
    if (!instance || !focusId || framed.current === nonce) return
    const feature = data.features.find((f) => f.properties.public_id === focusId)
    if (!feature) return
    const bounds = boundsOfTransito(feature)
    if (!bounds) return
    framed.current = nonce
    const [west, south, east, north] = bounds
    if (west === east && south === north) {
      instance.easeTo({
        center: [west, south],
        zoom: Math.max(instance.getZoom(), 15),
        offset: focusOffset(),
        duration: 800,
      })
      return
    }
    instance.fitBounds(bounds, { padding: fitPadding(), maxZoom: FOCO_ZOOM_MAX, duration: 800 })
  }, [instance, focusId, nonce, data])

  if (!hasMounted) return null
  return (
    <Source id={TRANSITO_SOURCE_ID} type="geojson" data={data}>
      <Layer beforeId={TRANSITO_BEFORE_ID} {...specs.casing} />
      <Layer beforeId={TRANSITO_BEFORE_ID} {...specs.focus} />
      <Layer beforeId={TRANSITO_BEFORE_ID} {...specs.line} />
      <Layer beforeId={TRANSITO_BEFORE_ID} {...specs.point} />
    </Source>
  )
}
