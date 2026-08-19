import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AttributionControl,
  GeolocateControl,
  Layer,
  Map,
  NavigationControl,
  ScaleControl,
  Source,
} from 'react-map-gl/maplibre'
import type {
  ErrorEvent,
  MapLayerMouseEvent,
  MapRef,
} from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'
// Debe ejecutarse antes de que se instancie el mapa: sin esto el worker queda
// apuntando a una URL inexistente en produccion y el lienzo sale en blanco.
import '@/lib/maplibreWorker'

import type { SeismicEvent } from '@/api/seismicTypes'
import type { Incident } from '@/api/types'
import {
  INITIAL_VIEW_STATE,
  MAP_ATTRIBUTION,
  MAP_STYLE_URL,
  REGION_BOUNDS,
} from '@/config/map'
import { toFeatureCollection } from '@/lib/geojson'
import { toSeismicFeatureCollection } from '@/lib/seismicGeojson'
import { attachMapDiagnostics } from '@/lib/mapDiagnostics'
import {
  INCIDENT_HIT_LAYER_ID,
  INCIDENT_SOURCE_ID,
  alertHaloLayer,
  casingLayer,
  closedRingLayer,
  coreLayer,
  hitLayer,
  selectedLayer,
  unverifiedLayer,
} from './incidentLayers'
import {
  SEISMIC_HIT_LAYER_ID,
  SEISMIC_SOURCE_ID,
  seismicCoreLayer,
  seismicHitLayer,
  seismicRingLayer,
  seismicSelectedLayer,
} from './seismicLayers'

interface IncidentMapProps {
  incidents: readonly Incident[]
  seismic: readonly SeismicEvent[]
  /** Capas encendidas desde el control de capas. */
  showIncidents: boolean
  showSeismic: boolean
  selectedCode: string | null
  selectedUsgsId: string | null
  onSelect: (code: string | null) => void
  onSelectSeismic: (usgsId: string | null) => void
}

export function IncidentMap({
  incidents,
  seismic,
  showIncidents,
  showSeismic,
  selectedCode,
  selectedUsgsId,
  onSelect,
  onSelectSeismic,
}: IncidentMapProps) {
  const mapRef = useRef<MapRef>(null)
  const [hovering, setHovering] = useState(false)

  // Se recalcula solo cuando cambia el arreglo de incidentes, no en cada
  // repintado: el polling entrega un arreglo nuevo cada minuto, no cada frame.
  const data = useMemo(() => toFeatureCollection(incidents), [incidents])
  const seismicData = useMemo(() => toSeismicFeatureCollection(seismic), [seismic])

  // Sólo las capas visibles reciben el toque. Si no se filtrara, un sismo
  // oculto seguiría capturando el clic sobre el incidente que hay debajo.
  const interactiveLayers = useMemo(() => {
    const ids: string[] = []
    if (showIncidents) ids.push(INCIDENT_HIT_LAYER_ID)
    if (showSeismic) ids.push(SEISMIC_HIT_LAYER_ID)
    return ids
  }, [showIncidents, showSeismic])

  const handleClick = useCallback(
    (event: MapLayerMouseEvent) => {
      // Los incidentes tienen prioridad sobre los sismos: si ambos caen bajo el
      // dedo, gana la emergencia. El orden de `interactiveLayerIds` no lo
      // garantiza, así que se resuelve explícitamente.
      const features = event.features ?? []
      const incident = features.find((f) => typeof f.properties?.['code'] === 'string')
      const quake = features.find((f) => typeof f.properties?.['usgs_id'] === 'string')

      if (!incident && quake) {
        onSelect(null)
        onSelectSeismic(String(quake.properties!['usgs_id']))
        return
      }

      const code = incident?.properties?.['code']
      if (typeof code !== 'string') {
        onSelect(null)
        onSelectSeismic(null)
        return
      }

      onSelectSeismic(null)
      onSelect(code)

      // La tarjeta ocupa el tercio inferior en teléfono. Centrar el incidente
      // sin compensar lo dejaria justo debajo de la tarjeta, que es donde no se
      // ve. El desplazamiento vertical lo saca de ahi.
      const map = mapRef.current
      if (map) {
        map.easeTo({
          center: event.lngLat,
          offset: [0, -Math.min(window.innerHeight * 0.18, 160)],
          duration: 450,
        })
      }
    },
    [onSelect, onSelectSeismic],
  )

  /**
   * Un mapa en blanco sin nada en consola es el peor modo de falla: no se sabe
   * si falló el estilo, el WebGL, el worker o el encuadre.
   *
   * Las sondas se enganchan en un efecto y no en `onLoad` a propósito: el fallo
   * más difícil de ver es justamente aquel en el que `load` no se dispara nunca.
   * Un handler de `load` no puede observar su propia ausencia; un `setTimeout`
   * registrado al montar, sí. Ver `lib/mapDiagnostics.ts`.
   */
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map) return
    return attachMapDiagnostics(map)
  }, [])

  const handleError = useCallback((event: ErrorEvent) => {
    console.error('[AlertaV/mapa] error', event.error ?? event)
  }, [])

  return (
    <Map
      ref={mapRef}
      initialViewState={INITIAL_VIEW_STATE}
      mapStyle={MAP_STYLE_URL}
      maxBounds={REGION_BOUNDS}
      minZoom={7}
      maxZoom={17}
      style={{ position: 'absolute', inset: 0 }}
      interactiveLayerIds={interactiveLayers}
      onClick={handleClick}
      onError={handleError}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      cursor={hovering ? 'pointer' : 'grab'}
      attributionControl={false}
      // Un mapa de emergencias se consulta en la calle y con una mano: los
      // gestos que rotan o inclinan solo estorban.
      dragRotate={false}
      pitchWithRotate={false}
      touchZoomRotate={{ around: 'center' }}
    >
      <AttributionControl compact customAttribution={MAP_ATTRIBUTION} />
      <NavigationControl position="top-right" showCompass={false} />
      <GeolocateControl
        position="top-right"
        trackUserLocation
        positionOptions={{ enableHighAccuracy: true }}
      />
      <ScaleControl position="bottom-left" unit="metric" />

      {/* Los sismos van debajo: son contexto, no el sujeto del mapa. */}
      {showSeismic && (
        <Source id={SEISMIC_SOURCE_ID} type="geojson" data={seismicData}>
          <Layer {...seismicRingLayer} />
          <Layer {...seismicCoreLayer} />
          <Layer {...seismicSelectedLayer(selectedUsgsId)} />
          <Layer {...seismicHitLayer} />
        </Source>
      )}

      {showIncidents && (
      <Source
        id={INCIDENT_SOURCE_ID}
        type="geojson"
        data={data}
        promoteId="code"
      >
        <Layer {...alertHaloLayer} />
        <Layer {...casingLayer} />
        <Layer {...coreLayer} />
        <Layer {...closedRingLayer} />
        <Layer {...unverifiedLayer} />
        <Layer {...selectedLayer(selectedCode)} />
        <Layer {...hitLayer} />
      </Source>
      )}
    </Map>
  )
}
